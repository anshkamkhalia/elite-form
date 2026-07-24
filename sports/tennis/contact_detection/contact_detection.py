# audio-based contact detection

import numpy as np

class ContactDetector:

    def __init__(self):
        self.n_contacts = 0
        self.hit = False
        self.contact_display_frames = 30
        self.last_display_hit_frame = -self.contact_display_frames
        self.cooldown_frames = int(0.15 * 30)
        self.last_hit_frame = -self.cooldown_frames

    def set_audio(self, audio, sr):
        self.audio = audio
        self.sr = sr

    def get_audio_at_frame(self, frame_idx):
        time = frame_idx/30
        sample_dix = int(time*self.sr)
        window = int(0.05 * self.sr)
        start = max(0, sample_dix - window // 2)
        end = min(len(self.audio), sample_dix + window // 2)
        return self.audio[start:end]
    
    def calculate_avg_energy(self):
        self.avg_energy = np.sqrt(np.mean(self.audio**2))
        self.min_energy = self.avg_energy + 0.075
    
    def detect_contact(self, frame_idx):

        self.calculate_avg_energy()
        current_audio = self.get_audio_at_frame(frame_idx)
        energy = np.sqrt(np.mean(current_audio**2)) # get RMS energy for audio sample

        score = 0

        if energy >= self.min_energy:
            score += 1

        if score >= 1 and (frame_idx - self.last_hit_frame > self.cooldown_frames):
            self.last_hit_frame = frame_idx
            self.n_contacts += 1

            return True
        
        return False