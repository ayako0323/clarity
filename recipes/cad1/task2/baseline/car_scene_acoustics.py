"""A class for the car acoustic environment."""
# pylint: disable=import-error
# pylint: disable=too-many-instance-attributes

import logging
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pyloudnorm as pyln
from scipy.io import wavfile
from scipy.signal import lfilter

from clarity.enhancer.compressor import Compressor
from clarity.enhancer.nalr import NALR
from clarity.utils.car_noise_simulator.carnoise_signal_generator import (
    CarNoiseSignalGenerator,
)

logger = logging.getLogger(__name__)


class CarSceneAcoustics:
    """
    A class for the car acoustic environment.

    Constants:
        ANECHOIC_HRTF (dict): A dictionary containing the names of the anechoic BRIRs
            for the following directions:
                0 degrees: front
                    - 000_left: The left channel of the BRIR for 0 degrees.
                    - 000_right: The right channel of the BRIR for 0 degrees.
                -90 degrees: left
                    - m90_left: The left channel of the BRIR for -90 degrees.
                    - m90_right: The right channel of the BRIR for -90 degrees.
                90 degrees: right
                    - p90_left: The left channel of the BRIR for 90 degrees.
                    - p90_right: The right channel of the BRIR for 90 degrees.
    """

    ANECHOIC_HRTF = {
        "000_left": "HR36_LSP01_CH1_Left.wav",
        "000_right": "HR36_LSP01_CH1_Right.wav",
        "m90_left": "HR0_LSP01_CH1_Left.wav",
        "m90_right": "HR0_LSP01_CH1_Right.wav",
        "p90_left": "HR72_LSP01_CH1_Left.wav",
        "p90_right": "HR72_LSP01_CH1_Right.wav",
    }

    def __init__(
        self,
        track_duration: int,
        sample_rate: int,
        hrtf_dir: str,
        config_nalr: dict,
        config_compressor: dict,
        extend_noise: float = 0.2,
    ):
        """
        Initializes the CarSceneAcoustics object.

        Args:
        track_duration (int): The duration of the audio track in seconds..
        sample_rate (int): The sample rate of the audio in Hz.
        hrtf_dir (str): The path to the directory containing the BRIR files.
        config_nalr (dict): The configuration for the NALR enhancer.
        config_compressor (dict): The configuration for the compressor.
        extend_noise (float): The factor by which to extend the duration of the car
            noise generated by the CarNoiseGenerator. Defaults to 0.2.
            This is to prevent the car noise from being shorter than the audio track.
        """

        self.track_duration = track_duration * (1 + extend_noise)
        self.sample_rate = sample_rate
        self.hrtf_dir = hrtf_dir

        self.preload_anechoic_hrtf(self.hrtf_dir)
        self.enhancer = NALR(**config_nalr)
        self.compressor = Compressor(**config_compressor)

        self.carnoise = CarNoiseSignalGenerator(
            duration_secs=self.track_duration,
            sample_rate=self.sample_rate,
        )
        self.loudness_meter = pyln.Meter(self.sample_rate)

    def preload_anechoic_hrtf(self, hrtf_dir: str) -> None:
        """
        Loads the Anechoic BRIRs from the eBrird database for the given directions.
        Using the following directions:
            0 degrees: front
            -90 degrees: left
            90 degrees: right

        Args:
            brird_dir (str): The path to the directory containing the BRIR files.
        """
        self.hrir_values = {}
        anechoic_hrtf_dir = Path(hrtf_dir) / "Anechoic" / "audio"

        for key, item in self.ANECHOIC_HRTF.items():
            self.hrir_values[key] = wavfile.read(anechoic_hrtf_dir / item)[1]

    def apply_hearing_aid(
        self, signal: np.ndarray, audiogram: np.ndarray, center_frequencies: np.ndarray
    ) -> np.ndarray:
        """
        Applies the hearing aid:
        It consists in NALR prescription and Compressor

        Args:
            signal (np.ndarray): The audio signal to be enhanced.
            audiogram (np.ndarray): An audiogram used to configure the NALR object.
            center_frequencies (np.ndarray): An array of center frequencies
                used to configure the NALR object.

        Returns:
            np.ndarray: The enhanced audio signal.
        """
        nalr_fir, _ = self.enhancer.build(audiogram, center_frequencies)
        signal = self.enhancer.apply(nalr_fir, signal)
        signal, _, _ = self.compressor.process(signal)
        return signal

    def add_anechoic_hrtf(self, noise_signal: np.ndarray) -> np.ndarray:
        """
        Adds the Anechoic HRTF to the noise signal.
        Args:
            noise_signal: A numpy array representing the different components
                of the car noise signal.

        Returns:
            np.ndarray: The noise signal with the Anechoic HRTF applied.

        """
        # Apply Anechoic HRTF to the noise signal
        # Engine first
        out_left = lfilter(self.hrir_values["000_left"], 1, noise_signal[0, :])
        our_right = lfilter(self.hrir_values["000_right"], 1, noise_signal[0, :])

        # noise processing hardwired for 2 noises
        out_left += lfilter(self.hrir_values["m90_left"], 1, noise_signal[1, :])
        our_right += lfilter(self.hrir_values["m90_right"], 1, noise_signal[1, :])

        # swap HRIR so this noise is on the other side
        out_left += lfilter(self.hrir_values["p90_left"], 1, noise_signal[2, :])
        our_right += lfilter(self.hrir_values["p90_right"], 1, noise_signal[2, :])

        return np.stack([out_left, our_right], axis=0)

    def get_car_noise(
        self,
        car_noise_params: dict,
    ) -> np.ndarray:
        """
        Generates car noise.

        Args:
            car_noise_params (dict): Car Noise Parameters as generated by
                Class CarNoiseParameterGenerator

        Returns:
            numpy.ndarray: A numpy array representing the different components
                of the car noise signal

        """
        return self.carnoise.generate_car_noise(
            noise_parameters=car_noise_params,
            number_noise_sources=2,
            commonness_factor=0,
        )

    def add_car_hrtf(self, signal: np.ndarray, hrir: dict) -> np.ndarray:
        """Add a head rotation transfer function using binaural room impulse
            response (BRIR) from eBrird.

        Args:
            signal (np.ndarray): a numpy array of shape (2, n_samples) containing the
                stereo audio signal.
            hrir: a dictionary containing the HRIR (head-related impulse response) filenames.

        Returns:
            A numpy array of shape (2, n_samples) containing the stereo audio signal with the
                BRIR added.

        """
        car_hrtf_path = Path(self.hrtf_dir) / "Car" / "audio"

        # HRTF from left speaker (LS03) and front mic in HA (CH1)
        hr_ls03_ch1_left = wavfile.read(
            car_hrtf_path / f"{hrir['LSP03_CH1_Left']}.wav"
        )[1]
        hr_ls03_ch1_right = wavfile.read(
            car_hrtf_path / f"{hrir['LSP03_CH1_Right']}.wav"
        )[1]

        # HRTF from right speaker (LS04) and front mic in HA (CH1)
        hr_ls04_ch1_left = wavfile.read(
            car_hrtf_path / f"{hrir['LSP04_CH1_Left']}.wav"
        )[1]
        hr_ls04_ch1_right = wavfile.read(
            car_hrtf_path / f"{hrir['LSP04_CH1_Right']}.wav"
        )[1]

        # add the BRIRs to the signal
        # Left Speaker (LS03)
        out_left = lfilter(hr_ls03_ch1_left, 1, signal[0, :])
        out_right = lfilter(hr_ls03_ch1_right, 1, signal[0, :])
        # Right Speaker (LS04)
        out_left += lfilter(hr_ls04_ch1_left, 1, signal[1, :])
        out_right += lfilter(hr_ls04_ch1_right, 1, signal[1, :])

        return np.stack([out_left, out_right], axis=0)

    def scale_signal_to_snr(
        self,
        signal: np.ndarray,
        reference_signal: np.ndarray = None,
        snr: Optional[float] = 0,
    ) -> np.ndarray:
        """
        Scales the target signal to the desired SNR.
        We transpose channel because pylodnorm operates
        on arrays with shape [n_samples, n_channels].

        Args:
            target_signal (np.ndarray): The target signal to scale.
            reference_signal (np.ndarray): The reference signal.
                If None, the reference signal is set to 0 LUFS.
            snr (float): The desired SNR gain in dB.
                If None, the target signal is scaled to the reference signal.

        Returns:
            np.ndarray: The scaled target signal.
        """
        # Ensure channels are in the correct dimension
        if reference_signal.shape[0] < reference_signal.shape[1]:
            reference_signal = reference_signal.T
        if signal.shape[0] < signal.shape[1]:
            signal = signal.T

        ref_signal_lufs = (
            0.0
            if reference_signal is None
            else self.loudness_meter.integrated_loudness(reference_signal)
        )

        signal_lufs = self.loudness_meter.integrated_loudness(signal)

        target_lufs = ref_signal_lufs + snr

        with warnings.catch_warnings(record=True):
            normalised_signal = pyln.normalize.loudness(
                signal, signal_lufs, target_lufs
            )

        # return to original shape
        return normalised_signal.T

    @staticmethod
    def add_two_signals(signal1: np.ndarray, signal2: np.ndarray) -> np.ndarray:
        """
        Adds two signals together.

        Args:
            signal1 (np.ndarray): The first signal.
            signal2 (np.ndarray): The second signal.

        Returns:
            np.ndarray: The sum of the two signals.
        """
        min_length = min(signal1.shape[1], signal2.shape[1])
        return signal1[:, :min_length] + signal2[:, :min_length]
