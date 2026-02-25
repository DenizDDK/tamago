import json
import logging
import os
import random
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pygame

WIDTH, HEIGHT = 320, 480
SPRITE_SIZE = 128
SPRITE_X = 96
SPRITE_Y = 70
FPS = 30
SAVE_PATH = Path('/home/pi/tamagotchi/save.json')
AUTOSAVE_MS = 60_000
SPRITES_DIR = Path('sprites')

PHASES = {
    'baby': range(1, 5),
    'kid': range(5, 10),
    'teen': range(10, 15),
    'adult': range(15, 21),
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger('tamagotchi')


@dataclass
class GameState:
    level: int = 1
    xp: int = 0
    hunger: int = 50
    happiness: int = 50
    energy: int = 50
    age_days: int = 0

    @property
    def current_phase(self) -> str:
        for phase, levels in PHASES.items():
            if self.level in levels:
                return phase
        return 'adult'


def clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def xp_needed(level: int) -> int:
    return level * 50


def apply_leveling(state: GameState) -> None:
    while state.level < 20 and state.xp >= xp_needed(state.level):
        state.xp -= xp_needed(state.level)
        state.level += 1
        logger.info('Level up! Now level %s (%s)', state.level, state.current_phase)


def robust_save(state: GameState) -> None:
    SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = SAVE_PATH.with_suffix('.tmp')
    with temp_path.open('w', encoding='utf-8') as f:
        json.dump(asdict(state), f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, SAVE_PATH)


def load_state() -> GameState:
    if not SAVE_PATH.exists():
        return GameState()
    try:
        with SAVE_PATH.open('r', encoding='utf-8') as f:
            data = json.load(f)
        return GameState(**data)
    except Exception as exc:
        logger.error('Failed to load save file: %s', exc)
        return GameState()


def color_for_value(value: int) -> tuple[int, int, int]:
    if value > 66:
        return (0, 180, 0)
    if value >= 33:
        return (30, 120, 255)
    return (210, 40, 40)


class SpriteManager:
    ACTIONS = ['idle', 'feed', 'play', 'cuddle']

    def __init__(self) -> None:
        self.cache: dict[str, dict[str, list[pygame.Surface]]] = {}

    def _load_image(self, path: Path) -> pygame.Surface:
        if path.exists():
            try:
                image = pygame.image.load(path.as_posix()).convert_alpha()
                return pygame.transform.smoothscale(image, (SPRITE_SIZE, SPRITE_SIZE))
            except Exception as exc:
                logger.error('Error loading sprite %s: %s', path, exc)
        else:
            logger.warning('Missing sprite file: %s', path)

        surf = pygame.Surface((SPRITE_SIZE, SPRITE_SIZE))
        surf.fill((60, 60, 60))
        pygame.draw.rect(surf, (255, 0, 0), surf.get_rect(), 3)
        font = pygame.font.SysFont(None, 20)
        txt = font.render('MISSING', True, (255, 255, 255))
        surf.blit(txt, txt.get_rect(center=surf.get_rect().center))
        return surf

    def load_phase(self, phase: str) -> None:
        if phase in self.cache:
            return
        phase_dir = SPRITES_DIR / phase
        frames: dict[str, list[pygame.Surface]] = {}
        for action in self.ACTIONS:
            frames[action] = []
            for idx in (1, 2):
                img = self._load_image(phase_dir / f'{action}_{idx}.png')
                frames[action].append(img)
        self.cache[phase] = frames

    def frame(self, phase: str, action: str, index: int) -> pygame.Surface:
        self.load_phase(phase)
        return self.cache[phase][action][index]


class Game:
    def __init__(self) -> None:
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption('Tamagotchi')
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 24)
        self.small_font = pygame.font.SysFont(None, 18)
        self.state = load_state()
        self.sprites = SpriteManager()

        self.running = True
        self.current_action = 'idle'
        self.frame_index = 0
        self.next_frame_change_ms = pygame.time.get_ticks() + 1000
        self.locked = False
        self.last_autosave_ms = pygame.time.get_ticks()

        self.buttons = {
            'FEED': pygame.Rect(10, 420, 95, 45),
            'PLAY': pygame.Rect(112, 420, 95, 45),
            'CUDDLE': pygame.Rect(215, 420, 95, 45),
        }
        self.power_button = pygame.Rect(255, 8, 58, 24)

    def handle_action(self, action: str) -> None:
        if action == 'FEED':
            self.state.hunger = clamp(self.state.hunger + 20)
            self.state.xp += 10
            self.start_animation('feed')
        elif action == 'PLAY':
            self.state.happiness = clamp(self.state.happiness + 20)
            self.state.energy = clamp(self.state.energy - 10)
            self.state.xp += 15
            self.start_animation('play')
        elif action == 'CUDDLE':
            self.state.happiness = clamp(self.state.happiness + 15)
            self.state.energy = clamp(self.state.energy - 5)
            self.state.xp += 12
            self.start_animation('cuddle')

        apply_leveling(self.state)
        robust_save(self.state)

    def start_animation(self, action: str) -> None:
        self.locked = True
        self.current_action = action
        self.frame_index = 0
        self.next_frame_change_ms = pygame.time.get_ticks() + random.randint(300, 500)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.running = False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if self.power_button.collidepoint(pos):
                self.shutdown_sequence()
                return
            if self.locked:
                return
            for name, rect in self.buttons.items():
                if rect.collidepoint(pos):
                    self.handle_action(name)
                    break

    def update(self) -> None:
        now = pygame.time.get_ticks()

        if self.current_action == 'idle':
            if now >= self.next_frame_change_ms:
                self.frame_index = 1 - self.frame_index
                self.next_frame_change_ms = now + 1000
        elif now >= self.next_frame_change_ms:
            if self.frame_index == 0:
                self.frame_index = 1
                self.next_frame_change_ms = now + random.randint(300, 500)
            else:
                self.current_action = 'idle'
                self.frame_index = 0
                self.next_frame_change_ms = now + 1000
                self.locked = False

        if now - self.last_autosave_ms >= AUTOSAVE_MS:
            robust_save(self.state)
            self.last_autosave_ms = now

    def draw_bar(self, label: str, value: int, top: int) -> None:
        label_surface = self.small_font.render(f'{label}: {value}%', True, (240, 240, 240))
        self.screen.blit(label_surface, (20, top))

        bar_rect = pygame.Rect(120, top + 2, 180, 16)
        pygame.draw.rect(self.screen, (70, 70, 70), bar_rect)
        fill_width = int((value / 100) * bar_rect.width)
        fill_rect = pygame.Rect(bar_rect.x, bar_rect.y, fill_width, bar_rect.height)
        pygame.draw.rect(self.screen, color_for_value(value), fill_rect)
        pygame.draw.rect(self.screen, (200, 200, 200), bar_rect, 1)

    def draw(self) -> None:
        self.screen.fill((25, 25, 35))

        sprite = self.sprites.frame(self.state.current_phase, self.current_action, self.frame_index)
        self.screen.blit(sprite, (SPRITE_X, SPRITE_Y))

        info = self.small_font.render(
            f'Level {self.state.level} ({self.state.current_phase})  XP {self.state.xp}/{xp_needed(self.state.level) if self.state.level < 20 else "MAX"}',
            True,
            (255, 255, 255),
        )
        self.screen.blit(info, (12, 22))

        self.draw_bar('Hunger', self.state.hunger, 230)
        self.draw_bar('Happiness', self.state.happiness, 262)
        self.draw_bar('Energy', self.state.energy, 294)

        for name, rect in self.buttons.items():
            pygame.draw.rect(self.screen, (60, 80, 150), rect, border_radius=6)
            pygame.draw.rect(self.screen, (220, 220, 220), rect, 2, border_radius=6)
            txt = self.font.render(name, True, (255, 255, 255))
            self.screen.blit(txt, txt.get_rect(center=rect.center))

        pygame.draw.rect(self.screen, (120, 40, 40), self.power_button, border_radius=5)
        ptxt = self.small_font.render('POWER', True, (255, 255, 255))
        self.screen.blit(ptxt, ptxt.get_rect(center=self.power_button.center))

        pygame.display.flip()

    def shutdown_sequence(self) -> None:
        logger.info('Power button pressed: saving and shutting down')
        robust_save(self.state)
        subprocess.run(['sync'], check=False)
        subprocess.run(['sudo', 'shutdown', '-h', 'now'], check=False)
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
        except Exception as exc:
            logger.error('Save on exit failed: %s', exc)
        pygame.quit()


def main() -> None:
    game = Game()

    def _signal_handler(signum, _frame):
        logger.info('Received signal %s; exiting gracefully', signum)
        game.running = False

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        game.run()
    finally:
        game.close()


if __name__ == '__main__':
    main()
