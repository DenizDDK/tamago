import json
import logging
import os
import random
import signal
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pygame

WIDTH, HEIGHT = 320, 480
SPRITE_SIZE = 128
SPRITE_X = 96
SPRITE_Y = 70
FPS = 30

SAVE_PATH = Path("/home/pi/tamagotchi/save.json")
AUTOSAVE_MS = 60_000

SPRITES_DIR = Path("sprites")

# Animation langsamer
IDLE_FRAME_MS = 2400
ACTION_FRAME_MS = (1800, 2600)

# Stat-Decay
DECAY_MS = 10_000
DECAY_HUNGER = -1
DECAY_HAPPINESS = -1
DECAY_LOVE = -1

# Textbox länger
DEFAULT_DIALOG_MS = 4200

PHASES = {
    "baby": range(1, 5),
    "kid": range(5, 10),
    "teen": range(10, 15),  # Level 10-14
    "adult": range(15, 21),
}

# Sprite actions mit 2 Frames
ACTIONS_2FR = ["idle", "feed", "play", "cuddle", "no_energy"]

# Kosten + Effekte
ACTION_RULES = {
    "FEED": {"cost": 2, "hunger": +22, "happiness": 0, "love": 0, "xp": 10, "anim": "feed"},
    "PLAY": {"cost": 10, "hunger": -2, "happiness": +22, "love": +4, "xp": 14, "anim": "play"},
    "CUDDLE": {"cost": 10, "hunger": -2, "happiness": +5, "love": +22, "xp": 14, "anim": "cuddle"},
}

DIALOG = {
    "baby": {
        "FEED": ["Nom nom... 🍼", "ssschhlupp", "nyomnyomnyom"],
        "PLAY": ["brbrrbrr", "brabrabra", "gugu gaga rassel lustig"],
        "CUDDLE": ["geil", "so waaarm :D", "I like this..."],
        "NO_ENERGY": ["wuaaah wuaah", "*baby kaputt warum tust du ihm das an", "wuaaaa"],
        "HUNGRY": ["Gugu gaga ich bin ein baby gib mir essen", "Feed me..", "Mein Bauch..."],
        "DEAD_LOVE": ["...  du liebst mich nicht."],
        "DEAD_PLAY": ["...  du spielst nicht mit mir."],
        "RESET": ["Du hast ein Baby Deno aufm Gewissen"],
    },
    "kid": {
        "FEED": ["Lecker lecker", "ich satt :D", "Danki"],
        "PLAY": ["Minecraft so cool yeah", "NOCHMAL!!", "SPIEL MIT MIR!!!"],
        "CUDDLE": ["Ich liebe dich", "Schatzi :3", "Mein Schatziii"],
        "NO_ENERGY": ["Keine Energie!", "Später...", "Ich kann nichtmal mehr zocken.. ;("],
        "HUNGRY": ["Ich bin hungrig... 😣", "Feed me...", "I'm starving..."],
        "DEAD_LOVE": ["... du liebst mich nicht."],
        "DEAD_PLAY": ["... du spielst nicht mit mir."],
        "RESET": ["Kind Deno tot weil du nicht auf ihn achten kannst"],
    },
    "teen": {
        "FEED": ["Jetzt ein Babak", "ein dicker Jibb zum chillen", "Noch ein boun!!!"],
        "PLAY": ["zweites zuhause :=)", "Ich muss die Pflanzen gießen :D", "Arbeiten.."],
        "CUDDLE": ["JOANA <3 <3 <3 ", "Mein engelchen :3", "ily"],
        "NO_ENERGY": ["nicht jetzt man", "subtile Hinweise dass ich kein bock hab", "pustekuchen vergiss es"],
        "HUNGRY": ["Ich bin hungrig... 😣", "Fütter mich..", "Warum kein Essen ich Hunger"],
        "DEAD_LOVE": ["...  du liebst mich nicht."],
        "DEAD_PLAY": ["...  du spielst nicht mit mir."],
        "RESET": ["Verkack es nicht nochmal!!!"],
    },
    "adult": {
        "FEED": ["Fleisch!", "Ich stopf soviel in den Mund wie es nur geht", "*Zu voller Mund zum reden*"],
        "PLAY": ["Jetz- MEIN AUTOOOO", "Ist das ein...", "BOOOMBOCLAT"],
        "CUDDLE": ["MEIN SCHATZI", "DU ENGEL", "Ich brauche dich für immer"],
        "NO_ENERGY": ["Digga bin tot", "nein vergiss es", "Später vll"],
        "HUNGRY": ["Du nixgönner", "Ich würde selber kochen gerade wenn ich kein Tamagotchi wäre", "HUnger du idiot"],
        "DEAD_LOVE": ["...  du liebst mich nicht."],
        "DEAD_PLAY": ["...  du spielst nicht mit mir."],
        "RESET": ["Hier kannst du unendlich resetten aber im echten leben gibt es mich nur einmal.."],
    },
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tamagotchi")


def clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(value)))


def xp_needed(level: int) -> int:
    return 100 + (level - 1) * 25


def apply_leveling(state: "GameState") -> None:
    while state.level < 20 and state.xp >= xp_needed(state.level):
        state.xp -= xp_needed(state.level)
        state.level += 1
        logger.info("Level up! New level: %s", state.level)


def bar_color(value: int) -> str:
    if value >= 70:
        return "green"
    if value >= 35:
        return "orange"
    return "red"


def color_for_value(value: int):
    if value >= 70:
        return (60, 170, 90)
    if value >= 35:
        return (200, 170, 60)
    return (200, 80, 70)


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def robust_save(state: "GameState") -> None:
    ensure_parent_dir(SAVE_PATH)
    tmp = SAVE_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        tmp.replace(SAVE_PATH)
        logger.info("Saved state to %s", SAVE_PATH)
    except Exception:
        logger.exception("Failed to save state")


def load_state() -> "GameState":
    try:
        if SAVE_PATH.exists():
            data = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
            return GameState(**data)
    except Exception:
        logger.exception("Failed to load state, using defaults")
    return GameState()


@dataclass
class GameState:
    level: int = 1
    xp: int = 0
    hunger: int = 50
    happiness: int = 50
    love: int = 50
    energy: int = 50
    age_days: int = 0
    dead: bool = False

    @property
    def current_phase(self) -> str:
        for phase, levels in PHASES.items():
            if self.level in levels:
                return phase
        return "adult"


class SpriteManager:
    """
    Pro Phase-Ordner:
      idle_1.png idle_2.png
      feed_1.png feed_2.png
      play_1.png play_2.png
      cuddle_1.png cuddle_2.png
      no_energy_1.png no_energy_2.png
      dead.png  (ein Frame)
    Nur teen optional:
      idle_3.png (secret idle)
    """

    def __init__(self) -> None:
        # cache[phase][action] = list[Surface] (1..n frames)
        self.cache: Dict[str, Dict[str, List[pygame.Surface]]] = {}

    def _placeholder(self, label: str) -> pygame.Surface:
        surf = pygame.Surface((SPRITE_SIZE, SPRITE_SIZE), pygame.SRCALPHA)
        surf.fill((60, 60, 80))
        pygame.draw.rect(surf, (220, 220, 220), surf.get_rect(), 2)
        font = pygame.font.SysFont(None, 18)
        txt = font.render(label, True, (255, 255, 255))
        surf.blit(txt, txt.get_rect(center=surf.get_rect().center))
        return surf

    def _load_image(self, path: Path) -> pygame.Surface:
        try:
            img = pygame.image.load(str(path)).convert_alpha()
            img = pygame.transform.smoothscale(img, (SPRITE_SIZE, SPRITE_SIZE))
            return img
        except Exception:
            logger.warning("Missing sprite: %s", path)
            return self._placeholder(path.stem)

    def load_phase(self, phase: str) -> None:
        if phase in self.cache:
            return

        frames: Dict[str, List[pygame.Surface]] = {}
        phase_dir = SPRITES_DIR / phase

        # 2-frame actions
        for action in ACTIONS_2FR:
            frames[action] = [
                self._load_image(phase_dir / f"{action}_1.png"),
                self._load_image(phase_dir / f"{action}_2.png"),
            ]

        # secret idle_3 (optional, only if exists)
        idle3 = phase_dir / "idle_3.png"
        if idle3.exists():
            frames["idle"].append(self._load_image(idle3))  # index 2

        # dead is a single file per phase
        frames["dead"] = [self._load_image(phase_dir / "dead.png")]

        self.cache[phase] = frames

    def frame(self, phase: str, action: str, index: int) -> pygame.Surface:
        self.load_phase(phase)
        if action not in self.cache[phase]:
            action = "idle"
        action_frames = self.cache[phase][action]
        if not action_frames:
            return self._placeholder(f"{phase}:{action}")
        if len(action_frames) == 1:
            return action_frames[0]
        # wrap index for safety
        index = index % len(action_frames)
        return action_frames[index]

    def has_idle3(self, phase: str) -> bool:
        self.load_phase(phase)
        return len(self.cache[phase].get("idle", [])) >= 3


class Game:
    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption("Tamagotchi")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 22)
        self.small_font = pygame.font.SysFont(None, 18)

        self.state = load_state()
        self.sprites = SpriteManager()

        self.running = True
        self.current_action = "idle"
        self.frame_index = 0
        self.next_frame_change_ms = pygame.time.get_ticks() + IDLE_FRAME_MS
        self.locked = False

        self.last_autosave_ms = pygame.time.get_ticks()
        self.last_decay_ms = pygame.time.get_ticks()

        # Energy float for accurate regen timings
        self.energy_float = float(self.state.energy)
        self.last_energy_update_ms = pygame.time.get_ticks()

        self.hunger_warning_shown = False

        self.status_message = ""
        self.status_message_until_ms = 0

        self.buttons = {
            "FEED": pygame.Rect(10, 420, 70, 45),
            "PLAY": pygame.Rect(86, 420, 70, 45),
            "CUDDLE": pygame.Rect(162, 420, 70, 45),
            "RESET": pygame.Rect(238, 420, 70, 45),
        }
        self.power_button = pygame.Rect(255, 8, 58, 24)

    def set_status(self, msg: str, ms: int = DEFAULT_DIALOG_MS) -> None:
        self.status_message = msg
        self.status_message_until_ms = pygame.time.get_ticks() + ms

    def say(self, key: str, ms: int = DEFAULT_DIALOG_MS) -> None:
        phase = self.state.current_phase
        options = DIALOG.get(phase, {}).get(key, [])
        if not options:
            self.set_status(key, ms)
            return
        self.set_status(random.choice(options), ms)

    def reset_game(self) -> None:
        self.state = GameState()
        self.energy_float = float(self.state.energy)
        self.last_energy_update_ms = pygame.time.get_ticks()

        self.current_action = "idle"
        self.locked = False
        self.frame_index = 0
        self.next_frame_change_ms = pygame.time.get_ticks() + IDLE_FRAME_MS

        self.hunger_warning_shown = False

        robust_save(self.state)
        self.say("RESET", 3000)

    def effective_action_for_sprite(self) -> str:
        if self.state.dead:
            return "dead"
        # no_energy idle: wenn idle und nicht genug für PLAY/CUDDLE (10)
        if self.current_action == "idle" and self.state.energy < 10:
            return "no_energy"
        return self.current_action

    def use_secret_idle3(self) -> bool:
        # ✅ nur Level 10-14 (= teen) + voller Hunger
        return (
            self.state.current_phase == "teen"
            and self.state.hunger >= 100
            and self.current_action == "idle"
            and not self.state.dead
            and self.sprites.has_idle3("teen")
        )

    def energy_fill_minutes(self) -> float:
        """0 -> 100 in X Minuten abhängig von Farben (Hunger/Happiness/Love)."""
        hunger = self.state.hunger
        happiness = self.state.happiness
        love = self.state.love

        if hunger <= 0:
            return 60.0
        if hunger < 35:
            return 20.0

        colors = [bar_color(hunger), bar_color(happiness), bar_color(love)]
        orange_count = sum(1 for c in colors if c == "orange")

        if all(c == "green" for c in colors):
            return 7.5
        if orange_count == 1:
            return 10.0
        if orange_count >= 2:
            return 12.5

        # fallback (z.B. love/happy rot, hunger aber nicht rot)
        return 12.5

    def update_energy(self) -> None:
        """Kontinuierliche Energy-Regeneration, nur idle + nicht locked + nicht dead."""
        now = pygame.time.get_ticks()
        dt_ms = now - self.last_energy_update_ms
        self.last_energy_update_ms = now

        if self.state.dead:
            return
        if self.current_action != "idle" or self.locked:
            return
        if self.state.energy >= 100:
            self.energy_float = 100.0
            return

        minutes = self.energy_fill_minutes()
        rate_per_ms = 100.0 / (minutes * 60_000.0)

        self.energy_float = min(100.0, self.energy_float + rate_per_ms * dt_ms)
        new_energy = clamp(self.energy_float)
        if new_energy != self.state.energy:
            self.state.energy = new_energy

    def handle_action(self, action: str) -> None:
        if self.state.dead:
            if action == "RESET":
                self.reset_game()
            return

        if action == "RESET":
            return

        rule = ACTION_RULES.get(action)
        if not rule:
            return

        cost = int(rule.get("cost", 0))
        if self.state.energy < cost:
            self.say("NO_ENERGY", 2600)
            return

        # Pay cost
        self.energy_float = float(self.state.energy)
        self.state.energy = clamp(self.state.energy - cost)
        self.energy_float = float(self.state.energy)

        # Apply effects
        self.state.hunger = clamp(self.state.hunger + int(rule.get("hunger", 0)))
        self.state.happiness = clamp(self.state.happiness + int(rule.get("happiness", 0)))
        self.state.love = clamp(self.state.love + int(rule.get("love", 0)))
        self.state.xp += int(rule.get("xp", 0))

        self.start_animation(str(rule.get("anim", "idle")))
        self.say(action, DEFAULT_DIALOG_MS)

        apply_leveling(self.state)
        robust_save(self.state)

    def start_animation(self, action: str) -> None:
        self.locked = True
        self.current_action = action
        self.frame_index = 0
        self.next_frame_change_ms = pygame.time.get_ticks() + random.randint(*ACTION_FRAME_MS)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.running = False
                return
            if event.key == pygame.K_r and self.state.dead:
                self.reset_game()
                return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos

            if self.power_button.collidepoint(pos):
                self.shutdown_sequence()
                return

            if self.locked:
                return

            if self.state.dead:
                if self.buttons["RESET"].collidepoint(pos):
                    self.reset_game()
                return

            for name in ("FEED", "PLAY", "CUDDLE"):
                if self.buttons[name].collidepoint(pos):
                    self.handle_action(name)
                    break

    def update(self) -> None:
        now = pygame.time.get_ticks()

        # Animation frame switching
        if self.current_action == "idle":
            if now >= self.next_frame_change_ms:
                self.frame_index = 1 - self.frame_index
                self.next_frame_change_ms = now + IDLE_FRAME_MS
        elif now >= self.next_frame_change_ms:
            if self.frame_index == 0:
                self.frame_index = 1
                self.next_frame_change_ms = now + random.randint(*ACTION_FRAME_MS)
            else:
                self.current_action = "idle"
                self.frame_index = 0
                self.next_frame_change_ms = now + IDLE_FRAME_MS
                self.locked = False

        # Stat decay
        if not self.state.dead and now - self.last_decay_ms >= DECAY_MS:
            self.state.hunger = clamp(self.state.hunger + DECAY_HUNGER)
            self.state.happiness = clamp(self.state.happiness + DECAY_HAPPINESS)
            self.state.love = clamp(self.state.love + DECAY_LOVE)
            self.last_decay_ms = now

        # Hunger=0 warning (einmal)
        if not self.state.dead and self.state.hunger <= 0 and not self.hunger_warning_shown:
            self.say("HUNGRY", 5200)
            self.hunger_warning_shown = True
        if not self.state.dead and self.state.hunger > 0:
            self.hunger_warning_shown = False

        # Death rule: hunger == 0 AND (love red OR happiness red)
        if not self.state.dead and self.state.hunger <= 0:
            love_red = self.state.love < 35
            happy_red = self.state.happiness < 35
            if love_red or happy_red:
                self.state.dead = True
                self.current_action = "idle"
                self.locked = False
                self.frame_index = 0
                self.next_frame_change_ms = now + IDLE_FRAME_MS

                if love_red:
                    self.say("DEAD_LOVE", 7000)
                else:
                    self.say("DEAD_PLAY", 7000)

                robust_save(self.state)

        # Energy regen (dynamisch)
        self.update_energy()

        # Autosave
        if now - self.last_autosave_ms >= AUTOSAVE_MS:
            robust_save(self.state)
            self.last_autosave_ms = now

        # Status timeout
        if now >= self.status_message_until_ms:
            self.status_message = ""

    def draw_bar(self, label: str, value: int, top: int) -> None:
        label_surface = self.small_font.render(f"{label}: {value}%", True, (240, 240, 240))
        self.screen.blit(label_surface, (20, top))

        bar_rect = pygame.Rect(120, top + 2, 180, 16)
        pygame.draw.rect(self.screen, (70, 70, 70), bar_rect)
        fill_width = int((value / 100) * bar_rect.width)
        fill_rect = pygame.Rect(bar_rect.x, bar_rect.y, fill_width, bar_rect.height)
        pygame.draw.rect(self.screen, color_for_value(value), fill_rect)
        pygame.draw.rect(self.screen, (200, 200, 200), bar_rect, 1)

    def draw(self) -> None:
        self.screen.fill((25, 25, 35))

        action_for_sprite = self.effective_action_for_sprite()

        # ✅ secret idle_3 override
        if self.use_secret_idle3():
            sprite_index = 2  # idle_3.png
        else:
            sprite_index = self.frame_index

        sprite = self.sprites.frame(self.state.current_phase, action_for_sprite, sprite_index)
        self.screen.blit(sprite, (SPRITE_X, SPRITE_Y))

        xp_text = "MAX" if self.state.level >= 20 else f"{self.state.xp}/{xp_needed(self.state.level)}"
        info = self.small_font.render(
            f"Level {self.state.level} ({self.state.current_phase})  XP {xp_text}",
            True,
            (255, 255, 255),
        )
        self.screen.blit(info, (12, 22))

        self.draw_bar("Hunger", self.state.hunger, 220)
        self.draw_bar("Happiness", self.state.happiness, 252)
        self.draw_bar("Love", self.state.love, 284)
        self.draw_bar("Energy", self.state.energy, 316)

        # Dialog bubble
        if self.status_message:
            box = pygame.Rect(12, 350, 296, 42)
            pygame.draw.rect(self.screen, (35, 35, 45), box, border_radius=8)
            pygame.draw.rect(self.screen, (200, 200, 200), box, 1, border_radius=8)
            txt = self.small_font.render(self.status_message, True, (240, 240, 240))
            self.screen.blit(txt, txt.get_rect(center=box.center))

        # Buttons
        if self.state.dead:
            rect = self.buttons["RESET"]
            pygame.draw.rect(self.screen, (60, 80, 150), rect, border_radius=6)
            pygame.draw.rect(self.screen, (220, 220, 220), rect, 2, border_radius=6)
            txt = self.font.render("RESET (R)", True, (255, 255, 255))
            self.screen.blit(txt, txt.get_rect(center=rect.center))

            for name in ("FEED", "PLAY", "CUDDLE"):
                rect = self.buttons[name]
                pygame.draw.rect(self.screen, (75, 75, 75), rect, border_radius=6)
                pygame.draw.rect(self.screen, (220, 220, 220), rect, 2, border_radius=6)
                txt = self.small_font.render(name, True, (170, 170, 170))
                self.screen.blit(txt, txt.get_rect(center=rect.center))
        else:
            for name in ("FEED", "PLAY", "CUDDLE"):
                rect = self.buttons[name]
                cost = int(ACTION_RULES.get(name, {}).get("cost", 0))
                disabled = self.state.energy < cost

                button_color = (75, 75, 75) if disabled else (60, 80, 150)
                text_color = (170, 170, 170) if disabled else (255, 255, 255)

                pygame.draw.rect(self.screen, button_color, rect, border_radius=6)
                pygame.draw.rect(self.screen, (220, 220, 220), rect, 2, border_radius=6)

                label = f"{name} ({cost})" if cost > 0 else name
                txt = self.small_font.render(label, True, text_color)
                self.screen.blit(txt, txt.get_rect(center=rect.center))

            rect = self.buttons["RESET"]
            pygame.draw.rect(self.screen, (75, 75, 75), rect, border_radius=6)
            pygame.draw.rect(self.screen, (220, 220, 220), rect, 2, border_radius=6)
            txt = self.small_font.render("RESET", True, (170, 170, 170))
            self.screen.blit(txt, txt.get_rect(center=rect.center))

        # Power button
        pygame.draw.rect(self.screen, (120, 40, 40), self.power_button, border_radius=5)
        ptxt = self.small_font.render("POWER", True, (255, 255, 255))
        self.screen.blit(ptxt, ptxt.get_rect(center=self.power_button.center))

        pygame.display.flip()

    def shutdown_sequence(self) -> None:
        logger.info("Power button pressed: saving and shutting down")
        self.set_status("Saving...", 1200)
        robust_save(self.state)

        try:
            subprocess.run(["sync"], check=False)
        except Exception:
            pass

        if os.name != "nt":
            try:
                subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)
            except Exception:
                logger.warning("Shutdown command failed; exiting only.")
        self.running = False

    def run(self) -> None:
        while self.running:
            for event in pygame.event.get():
                self.handle_event(event)
            self.update()
            self.draw()
            self.clock.tick(FPS)

    def close(self) -> None:
        try:
            robust_save(self.state)
        finally:
            pygame.quit()


def _install_signal_handlers(game: Game) -> None:
    def _handler(signum, frame):
        logger.info("Signal %s received, exiting.", signum)
        game.running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


def main() -> int:
    game = Game()
    _install_signal_handlers(game)

    try:
        game.run()
    except Exception:
        logger.exception("Fatal error")
        return 1
    finally:
        game.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
