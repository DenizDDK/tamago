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
IDLE_FRAME_MS = 1400
ACTION_FRAME_MS = (900, 1300)

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
@@ -128,156 +130,170 @@ class SpriteManager:
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
        self.next_frame_change_ms = pygame.time.get_ticks() + IDLE_FRAME_MS
        self.locked = False
        self.last_autosave_ms = pygame.time.get_ticks()

        self.buttons = {
            'FEED': pygame.Rect(10, 420, 95, 45),
            'PLAY': pygame.Rect(112, 420, 95, 45),
            'CUDDLE': pygame.Rect(215, 420, 95, 45),
        }
        self.power_button = pygame.Rect(255, 8, 58, 24)

    def handle_action(self, action: str) -> None:
        if self.state.energy <= 0:
            logger.info('Action %s ignored: energy is depleted', action)
            return

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
        self.next_frame_change_ms = pygame.time.get_ticks() + random.randint(*ACTION_FRAME_MS)

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
                self.next_frame_change_ms = now + IDLE_FRAME_MS
        elif now >= self.next_frame_change_ms:
            if self.frame_index == 0:
                self.frame_index = 1
                self.next_frame_change_ms = now + random.randint(*ACTION_FRAME_MS)
            else:
                self.current_action = 'idle'
                self.frame_index = 0
                self.next_frame_change_ms = now + IDLE_FRAME_MS
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

        if self.state.energy <= 0:
            warning_box = pygame.Rect(12, 324, 296, 34)
            pygame.draw.rect(self.screen, (120, 25, 25), warning_box, border_radius=6)
            pygame.draw.rect(self.screen, (255, 160, 160), warning_box, 2, border_radius=6)
            exhausted = self.small_font.render('OUT OF ENERGY - ACTIONS LOCKED', True, (255, 220, 220))
            self.screen.blit(exhausted, exhausted.get_rect(center=warning_box.center))

        actions_disabled = self.state.energy <= 0
        for name, rect in self.buttons.items():
            button_color = (75, 75, 75) if actions_disabled else (60, 80, 150)
            text_color = (170, 170, 170) if actions_disabled else (255, 255, 255)
            pygame.draw.rect(self.screen, button_color, rect, border_radius=6)
            pygame.draw.rect(self.screen, (220, 220, 220), rect, 2, border_radius=6)
            txt = self.font.render(name, True, text_color)
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
