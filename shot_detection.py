"""Schnitterkennung (PySceneDetect) und Aufteilung in Analyse-Einheiten.

Eine "Einheit" (Unit) ist das, was Gemini einzeln in voller Schema-Tiefe
beschreibt: entweder ein ganzer Shot (<= 5 s) oder ein Teilfenster eines
längeren Shots. Shots > 5 s werden in gleich große Fenster <= 5 s geteilt
und als "Teil x/y von Shot N" gekennzeichnet.
"""
import math
from typing import List, Optional

from scenedetect import SceneManager, open_video
from scenedetect.detectors import ContentDetector

import config


class Shot(object):
    def __init__(self, number, start, end):
        self.number = number
        self.start = start
        self.end = end

    @property
    def duration(self):
        return self.end - self.start


class Unit(object):
    """Eine Analyse-Einheit: ganzer Shot oder 5-Sekunden-Fenster eines Shots."""

    def __init__(self, shot_number, start, end, window_index=None, window_total=None):
        self.shot_number = shot_number
        self.start = start
        self.end = end
        self.window_index = window_index  # 1-basiert, None = ganzer Shot
        self.window_total = window_total

    @property
    def duration(self):
        return self.end - self.start

    @property
    def label(self):
        if self.window_index is None:
            return "Shot %d" % self.shot_number
        return "Shot %d – Teil %d/%d" % (self.shot_number, self.window_index, self.window_total)

    @property
    def uid(self):
        if self.window_index is None:
            return "%d" % self.shot_number
        return "%d.%d" % (self.shot_number, self.window_index)


def detect_shots(video_path, duration, threshold=config.DETECT_THRESHOLD_DEFAULT):
    """Cuts erkennen. Keine Cuts -> ganzes Video als ein Shot."""
    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(
        threshold=float(threshold),
        min_scene_len=config.DETECT_MIN_SCENE_LEN_FRAMES,
    ))
    manager.detect_scenes(video, show_progress=False)
    scene_list = manager.get_scene_list()

    shots = []
    if not scene_list:
        shots.append(Shot(1, 0.0, duration))
    else:
        for i, (start_tc, end_tc) in enumerate(scene_list, start=1):
            shots.append(Shot(i, start_tc.get_seconds(), end_tc.get_seconds()))
        # Sicherstellen, dass das Ende des letzten Shots das Videoende ist
        if shots and abs(shots[-1].end - duration) > 0.5:
            shots[-1].end = max(shots[-1].end, duration)
    return shots


def build_units(shots):
    """Shots > 5 s in gleich große Fenster <= 5 s teilen und kennzeichnen."""
    units = []  # type: List[Unit]
    for shot in shots:
        if shot.duration <= config.SUBSHOT_WINDOW_SECONDS + 0.05:
            units.append(Unit(shot.number, shot.start, shot.end))
            continue
        n = int(math.ceil(shot.duration / config.SUBSHOT_WINDOW_SECONDS))
        win = shot.duration / n
        for k in range(n):
            w_start = shot.start + k * win
            w_end = shot.end if k == n - 1 else shot.start + (k + 1) * win
            units.append(Unit(shot.number, w_start, w_end, window_index=k + 1, window_total=n))
    return units


def plan_blocks(units, video_duration, keyframe_mode=False):
    """Einheiten in möglichst wenige Request-Blöcke packen.

    Normalfall: alles in einem Block (= 1 Gemini-Request). Nur wenn das Video
    zu lang ist (> BLOCK_MAX_SECONDS) oder zu viele Einheiten hat
    (Schutz vor abgeschnittener Antwort), wird in wenige große Blöcke
    geteilt – NIE ein Request pro Shot. Im Keyframe-Modus gilt ein kleineres
    Einheiten-Limit, damit die Inline-JPEGs unter dem 20-MB-Request-Limit bleiben.
    """
    max_units = config.KEYFRAME_BLOCK_MAX_UNITS if keyframe_mode else config.BLOCK_MAX_UNITS
    if video_duration <= config.BLOCK_MAX_SECONDS and len(units) <= max_units:
        return [units]

    blocks = []
    current = []  # type: List[Unit]
    for unit in units:
        if current:
            block_span = unit.end - current[0].start
            if len(current) >= max_units or block_span > config.BLOCK_MAX_SECONDS:
                blocks.append(current)
                current = []
        current.append(unit)
    if current:
        blocks.append(current)
    return blocks
