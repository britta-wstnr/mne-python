# -*- coding: utf-8 -*-
"""
DICS for power mapping
======================

In this tutorial, we're going to simulate two signals originating from two
locations on the cortex. These signals will be sine waves, so we'll be looking
at oscillatory activity (as opposed to evoked activity).

We'll be using dynamic imaging of coherent sources (DICS) [1]_ to map out
spectral power along the cortex. Let's see if we can find our two simulated
sources.
"""
# Author: Marijn van Vliet <w.m.vanvliet@gmail.com>
#
# License: BSD (3-clause)

###############################################################################
# Setup
# -----
# We first import the required packages to run this tutorial and define a list
# of filenames for various things we'll be using.
import os.path as op
import numpy as np
from scipy.signal import welch, coherence
from mayavi import mlab
from matplotlib import pyplot as plt

import mne
from mne.simulation import simulate_raw
from mne.datasets import sample
from mne.minimum_norm import make_inverse_operator, apply_inverse
from mne.time_frequency import csd_morlet
from mne.beamformer import make_dics, apply_dics_csd

# Suppress irrelevant output
mne.set_log_level('ERROR')

# We use the MEG and MRI setup from the MNE-sample dataset
data_path = sample.data_path(download=False)
subjects_dir = op.join(data_path, 'subjects')
mri_path = op.join(subjects_dir, 'sample')

# Filenames for various files we'll be using
meg_path = op.join(data_path, 'MEG', 'sample')
raw_fname = op.join(meg_path, 'sample_audvis_raw.fif')
trans_fname = op.join(meg_path, 'sample_audvis_raw-trans.fif')
src_fname = op.join(mri_path, 'bem/sample-oct-6-src.fif')
bem_fname = op.join(mri_path, 'bem/sample-5120-5120-5120-bem-sol.fif')
fwd_fname = op.join(meg_path, 'sample_audvis-meg-eeg-oct-6-fwd.fif')
cov_fname = op.join(meg_path, 'sample_audvis-cov.fif')

# Seed for the random number generator
rand = np.random.RandomState(42)

###############################################################################
# Data simulation
# ---------------
#
# The following function generates a timeseries that contains an oscillator,
# whose frequency fluctuates a little over time, but stays close to 10 Hz.
# We'll use this function to generate our two signals.

sfreq = 50.  # Sampling frequency of the generated signal
times = np.arange(10. * sfreq) / sfreq  # 10 seconds of signal
n_times = len(times)


def coh_signal_gen():
    """Generate an oscillating signal.

    Returns
    -------
    signal : ndarray
        The generated signal.
    """
    t_rand = 0.001  # Variation in the instantaneous frequency of the signal
    std = 0.1  # Std-dev of the random fluctuations added to the signal
    base_freq = 10.  # Base frequency of the oscillators in Hertz
    n_times = len(times)

    # Generate an oscillator with varying frequency and phase lag.
    signal = np.sin(2.0 * np.pi *
                    (base_freq * np.arange(n_times) / sfreq +
                     np.cumsum(t_rand * rand.randn(n_times))))

    # Add some random fluctuations to the signal.
    signal += std * rand.randn(n_times)

    # Scale the signal to be in the right order of magnitude (~100 nAm)
    # for MEG data.
    signal *= 100e-9

    return signal


###############################################################################
# Let's simulate two timeseries and plot some basic information about them.
signal1 = coh_signal_gen()
signal2 = coh_signal_gen()

fig, axes = plt.subplots(2, 2, figsize=(8, 4))

# Plot the timeseries
ax = axes[0][0]
ax.plot(times, 1e9 * signal1, lw=0.5)
ax.set(xlabel='Time (s)', xlim=times[[0, -1]], ylabel='Amplitude (Am)',
       title='Signal 1')
ax = axes[0][1]
ax.plot(times, 1e9 * signal2, lw=0.5)
ax.set(xlabel='Time (s)', xlim=times[[0, -1]], title='Signal 2')

# Power spectrum of the first timeseries
f, p = welch(signal1, fs=sfreq, nperseg=128, nfft=256)
ax = axes[1][0]
# Only plot the first 100 frequencies
ax.plot(f[:100], 20 * np.log10(p[:100]), lw=1.)
ax.set(xlabel='Frequency (Hz)', xlim=f[[0, 99]],
       ylabel='Power (dB)', title='Power spectrum of signal 1')

# Compute the coherence between the two timeseries
f, coh = coherence(signal1, signal2, fs=sfreq, nperseg=100, noverlap=64)
ax = axes[1][1]
ax.plot(f[:50], coh[:50], lw=1.)
ax.set(xlabel='Frequency (Hz)', xlim=f[[0, 49]], ylabel='Coherence',
       title='Coherence between the timeseries')
fig.tight_layout()

###############################################################################
# Now we put the signals at two locations on the cortex. We construct a
# :class:`mne.SourceEstimate` object to store them in.
#
# The timeseries will have a part where the signal is active and a part where
# it is not. The techniques we'll be using in this tutorial depend on being
# able to contrast data that contains the signal of interest versus data that
# does not (i.e. it contains only noise).

# The locations on the cortex where the signal will originate from. These
# locations are indicated as vertex numbers.
source_vert1 = 146374
source_vert2 = 33830

# The timeseries at each vertex: one part signal, one part silence
timeseries1 = np.hstack([signal1, np.zeros_like(signal1)])
timeseries2 = np.hstack([signal2, np.zeros_like(signal2)])

# Construct a SourceEstimate object that describes the signal at the cortical
# level.
stc = mne.SourceEstimate(
    np.vstack((timeseries1, timeseries2)),  # The two timeseries
    vertices=[[source_vert1], [source_vert2]],  # Their locations
    tmin=0,
    tstep=1. / sfreq,
    subject='sample',  # We use the brain model of the MNE-Sample dataset
)

###############################################################################
# Before we simulate the sensor-level data, let's define a signal-to-noise
# ratio. You are encouraged to play with this parameter and see the effect of
# noise on our results.
snr = 1.  # Signal-to-noise ratio. Decrease to add more noise.

###############################################################################
# Now we run the signal through the forward model to obtain simulated sensor
# data. To save computation time, we'll only simulate gradiometer data. You can
# try simulating other types of sensors as well.
#
# Some noise is added based on the baseline noise covariance matrix from the
# sample dataset, scaled to implement the desired SNR.

# Read the info from the sample dataset. This defines the location of the
# sensors and such.
info = mne.io.read_info(raw_fname)
info.update(sfreq=sfreq, bads=[])

# Only use gradiometers
picks = mne.pick_types(info, meg='grad', stim=True, exclude=())
mne.pick_info(info, picks, copy=False)

# This is the raw object that will be used as a template for the simulation.
raw = mne.io.RawArray(np.zeros((info['nchan'], len(stc.times))), info)

# Define a covariance matrix for the simulated noise. In this tutorial, we use
# a simple diagonal matrix.
cov = mne.cov.make_ad_hoc_cov(info)
cov['data'] *= (20. / snr) ** 2  # Scale the noise to achieve the desired SNR

# Simulate the raw data, with a lowpass filter on the noise
raw = simulate_raw(raw, stc, trans_fname, src_fname, bem_fname, cov=cov,
                   random_state=rand, iir_filter=[4, -4, 0.8])


###############################################################################
# We create an :class:`mne.Epochs` object containing two trials: one with
# both noise and signal and one with just noise

t0 = raw.first_samp  # First sample in the data
t1 = t0 + n_times - 1  # Sample just before the second trial
epochs = mne.Epochs(
    raw,
    events=np.array([[t0, 0, 1], [t1, 0, 2]]),
    event_id=dict(signal=1, noise=2),
    tmin=0, tmax=10,
    preload=True,
)

# Plot some of the channels of the simulated data that are situated above one
# of our simulated sources.
picks = mne.pick_channels(epochs.ch_names, mne.read_selection('Left-frontal'))
epochs.plot(picks=picks)

###############################################################################
# Power mapping
# -------------
# With our simulated dataset ready, we can now pretend to be researchers that
# have just recorded this from a real subject and are going to study what parts
# of the brain communicate with each other.
#
# First, we'll create a source estimate of the MEG data. We'll use both a
# straightforward MNE-dSPM inverse solution for this, and the DICS beamformer
# which is specifically designed to work with oscillatory data.

###############################################################################
# Computing the inverse using MNE-dSPM:

# Compute the inverse operator
fwd = mne.read_forward_solution(fwd_fname)
inv = make_inverse_operator(epochs.info, fwd, cov)

# Apply the inverse model to the trial that also contains the signal.
s = apply_inverse(epochs['signal'].average(), inv)

# Take the root-mean square along the time dimension and plot the result.
s_rms = np.sqrt((s ** 2).mean())
brain = s_rms.plot('sample', subjects_dir=subjects_dir, hemi='both', figure=1,
                   size=600)

# Indicate the true locations of the source activity on the plot.
brain.add_foci(source_vert1, coords_as_verts=True, hemi='lh')
brain.add_foci(source_vert2, coords_as_verts=True, hemi='rh')

# Rotate the view and add a title.
mlab.view(0, 0, 550, [0, 0, 0])
mlab.title('MNE-dSPM inverse (RMS)', height=0.9)

###############################################################################
# We will now compute the cortical power map at 10 Hz. using a DICS beamformer.
# A beamformer will construct for each vertex a spatial filter that aims to
# pass activity originating from the vertex, while dampening activity from
# other sources as much as possible.
#
# The :func:`make_dics` function has many switches that offer precise control
# over the way the filter weights are computed. Currently, there is no clear
# consensus regarding the best approach. This is why we will demonstrate two
# approaches here:
#
#  1. The approach as described in [2]_, which first normalizes the forward
#     solution and computes a vector beamformer.
#  2. The scalar beamforming approach based on [3]_, which uses weight
#     normalization instead of normalizing the forward solution.

# Estimate the cross-spectral density (CSD) matrix on the trial containing the
# signal.
csd_signal = csd_morlet(epochs['signal'], frequencies=[10])

# Compute the spatial filters for each vertex, using two approaches.
filters_approach1 = make_dics(
    info, fwd, csd_signal, reg=0.05, pick_ori='max-power', normalize_fwd=True,
    inversion='single', weight_norm=None)
filters_approach2 = make_dics(
    info, fwd, csd_signal, reg=0.05, pick_ori='max-power', normalize_fwd=False,
    inversion='matrix', weight_norm='unit-noise-gain')

# Compute the DICS power map by applying the spatial filters to the CSD matrix.
power_approach1, f = apply_dics_csd(csd_signal, filters_approach1)
power_approach2, f = apply_dics_csd(csd_signal, filters_approach2)

# Plot the DICS power maps for both approaches.
for approach, power in enumerate([power_approach1, power_approach2], 1):
    brain = power.plot('sample', subjects_dir=subjects_dir, hemi='both',
                       figure=approach + 1, size=600)

    # Indicate the true locations of the source activity on the plot.
    brain.add_foci(source_vert1, coords_as_verts=True, hemi='lh')
    brain.add_foci(source_vert2, coords_as_verts=True, hemi='rh')

    # Rotate the view and add a title.
    mlab.view(0, 0, 550, [0, 0, 0])
    mlab.title('DICS power map, approach %d' % approach, height=0.9)

###############################################################################
# Excellent! All methods found our two simulated sources. Of course, with a
# signal-to-noise ratio (SNR) of 1, is isn't very hard to find them. You can
# try playing with the SNR and see how the MNE-dSPM and DICS approaches hold up
# in the presence of increasing noise. In the presence of more noise, you may
# need to increase the regularization parameter of the DICS beamformer.

###############################################################################
# References
# ----------
# .. [1] Gross et al. (2001). Dynamic imaging of coherent sources: Studying
#        neural interactions in the human brain. Proceedings of the National
#        Academy of Sciences, 98(2), 694-699.
#        https://doi.org/10.1073/pnas.98.2.694
# .. [2] van Vliet, et al. (2018) Analysis of functional connectivity and
#        oscillatory power using DICS: from raw MEG data to group-level
#        statistics in Python. bioRxiv, 245530. https://doi.org/10.1101/245530
# .. [3] Sekihara & Nagarajan. Adaptive spatial filters for electromagnetic
#        brain imaging (2008) Springer Science & Business Media
