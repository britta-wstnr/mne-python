"""
Source reconstruction using an LCMV beamformer
==============================================

This tutorial gives an overview of the beamformer method
and shows how to use an LCMV beamformer to reconstruct source
activity.

.. contents:: Page contents
   :local:
   :depth: 2
"""
# Author: Britta Westner <britta.wstnr@gmail.com>
#
# License: BSD (3-clause)

# sphinx_gallery_thumbnail_number = 5

import mne
from mne.datasets import sample
from mne.beamformer import make_lcmv, apply_lcmv

###############################################################################
# Introduction to beamformers
# ---------------------------
# A beamformer is a spatial filter that reconstructs source activity by
# scanning through a grid of pre-defined source points and estimating activity
# at each of those source points independently. A set of weights is
# constructed for each defined source location which defines the contribution
# of each sensor to this source.
# Beamformers are often used for their focal reconstructions and their ability
# to reconstruct deeper sources. They can also suppress external noise sources.
# The beamforming method applied in this tutorial is the linearly constrained
# minimum variance (LCMV) beamformer [1]_ which operates on time series.
# Frequency-resolved data can be reconstructed with the dynamic imaging of
# coherent sources (DICS) beamforming method [2]_.
# As we will see in the following, the spatial filter is computed from two
# ingredients: the forward model solution and the covariance matrix of the
# data.

###############################################################################
# Data processing
# ---------------
# We will use the sample data set for this tutorial and reconstruct source
# activity on the trials with left auditory stimulation.

data_path = sample.data_path()
subjects_dir = data_path + '/subjects'
raw_fname = data_path + '/MEG/sample/sample_audvis_filt-0-40_raw.fif'

# Read the raw data
raw = mne.io.read_raw_fif(raw_fname, preload=False)
raw.info['bads'] = ['MEG 2443']  # bad MEG channel

# Set up the epoching
event_id = 1  # those are the trials with left-ear auditory stimuli
tmin, tmax = -0.2, 0.5
events = mne.find_events(raw)

# pick relevant channels
raw.pick(['meg', 'eog'])  # pick channels of interest

# Create epochs
proj = False  # already applied
epochs = mne.Epochs(raw, events, event_id, tmin, tmax,
                    baseline=(None, 0), preload=True, proj=proj,
                    reject=dict(grad=4000e-13, mag=4e-12, eog=150e-6))

# Visualize averaged sensor space data
evoked = epochs.average()
evoked.plot_joint()

del raw  # save memory

###############################################################################
# Computing the covariance matrices
# ---------------------------------
# Spatial filters use the data covariance to estimate the filter
# weights. The data covariance matrix will be `inverted`_ during the spatial
# filter computation, so it is valuable to plot the covariance matrix and its
# eigenvalues to gauge whether matrix inversion will be possible.
# Also, because we want to combine different channel types (magnetometers and
# gradiometers), we need to account for the different amplitude scales of these
# channel types. To do this we will supply a noise covariance matrix to the
# beamformer, which will be used for whitening.
# The data covariance matrix should be estimated from a time window that
# includes the brain signal of interest,
# and incorporate enough samples for a stable estimate. Here, we use a time
# window incorporating the expected auditory response at around 100 ms post
# stimulus and extend the period to account for a low number of trials (72) and
# low sampling rate of 150 Hz.
# When looking at the covariance matrix plots, we can see that our data is
# slightly rank-deficient as the rank is not equal to the number of channels.
# Thus, we will have to regularize the covariance matrix before inverting it
# in the beamformer calculation. This can be achieved by setting the parameter
# ``reg=0.05`` when calculating the spatial filter with
# :func:`~mne.beamformer.make_lcmv`. This corresponds to loading the diagonal
# of the covariance matrix with 5% of the sensor power.


data_cov = mne.compute_covariance(epochs, tmin=0.01, tmax=0.25,
                                  method='empirical')
noise_cov = mne.compute_covariance(epochs, tmin=tmin, tmax=0,
                                   method='empirical')

data_cov.plot(epochs.info)

###############################################################################
# The forward model
# -----------------
# The forward model is the other important ingredient for the computation of a
# spatial filter. Here, we will load the forward model from disk; more
# information on how to create a forward model can be found in this tutorial:
# :ref:`tut-forward`.
# Note that beamformers are usually computed in a :class:`volume source space
# <mne.VolSourceEstimate>`, because estimating only cortical surface
# activation can misrepresent the data.

# Read forward model
fwd_fname = data_path + '/MEG/sample/sample_audvis-meg-vol-7-fwd.fif'
forward = mne.read_forward_solution(fwd_fname)


###############################################################################
# Handling depth bias
# -------------------
#
# The forward model solution is inherently biased toward superficial sources.
# When analyzing single conditions it is best to mitigate the depth bias
# somehow. There are several ways to do this:
#
# - :func:`mne.beamformer.make_lcmv` has a ``depth`` parameter that normalizes
#   the forward model prior to computing the spatial filters. See the docstring
#   for details.
# - "Unit noise gain" beamformers handle depth bias by normalizing the
#   weights of the spatial filter. Choose this by setting
#   ``weight_norm='unit-noise-gain'``.
# - "Neural activity index" beamformers handle depth bias by normalizing both
#   the weights and the estimated noise (see [1]_). Choose this by setting
#   ``weight_norm='nai'``.
#
# Note that when comparing conditions, the depth bias will cancel out and it is
# possible to set both parameters to ``None``.
#
#
# Compute the spatial filter
# --------------------------
# Now we can compute the spatial filter. We'll use "unit noise gain" to deal
# with depth bias, and will also
# optimize the orientation of the sources such that output
# power is maximized. This is achieved by setting ``pick_ori='max-power'``.
# This gives us one source estimate per source (i.e., voxel), which is known
# as a scalar beamformer. It is also possible to compute a vector beamformer,
# which gives back three estimates per voxel, corresponding to the three
# direction components of the source. This can be achieved by setting
# ``pick_ori='vector'`` and will yield a :class:`volume vector source estimate
# <mne.VolVectorSourceEstimate>`.

filters = make_lcmv(evoked.info, forward, data_cov, reg=0.05,
                    noise_cov=noise_cov, pick_ori='max-power',
                    weight_norm='unit-noise-gain', rank=None)

# You can save the filter for later use with:
# filters.save('filters-lcmv.h5')

###############################################################################
# Apply the spatial filter
# ------------------------
# The spatial filter can be applied to different data types: raw, epochs,
# evoked data or the data covariance matrix to gain a static image of power.
# The function to apply the spatial filter to :class:`~mne.Evoked` data is
# :func:`~mne.beamformer.apply_lcmv` which is
# what we will use here. The other functions are
# :func:`~mne.beamformer.apply_lcmv_raw`,
# :func:`~mne.beamformer.apply_lcmv_epochs`, and
# :func:`~mne.beamformer.apply_lcmv_cov`.

stc = apply_lcmv(evoked, filters, max_ori_out='signed')

###############################################################################
# Visualize the reconstructed source activity
# -------------------------------------------
# We can visualize the source estimate in different ways, e.g. as an overlay
# onto the MRI or as a glass brain.
# The plots show brain activity in the right temporal lobe around 100 ms post
# stimulus. This is expected given the left-ear auditory stimulation of the
# experiment.

lims = [0.3, 0.45, 0.6]

kwargs = dict(src=forward['src'], subject='sample', subjects_dir=subjects_dir,
              initial_time=0.087, verbose=True)

stc.plot(mode='stat_map', clim=dict(kind='value', pos_lims=lims), **kwargs)
###############################################################################
stc.plot(mode='glass_brain', clim=dict(kind='value', lims=lims), **kwargs)


###############################################################################
# References
# ----------
# .. [1] Van Veen et al. Localization of brain electrical activity via
#        linearly constrained minimum variance spatial filtering.
#        Biomedical Engineering (1997) vol. 44 (9) pp. 867--880
#
# .. [2] Gross et al. (2001) Dynamic imaging of coherent sources: Studying
#        neural interactions in the human brain.
#        PNAS vol. 98 (2) pp. 694-699. https://doi.org/10.1073/pnas.98.2.694
#
#
# .. LINKS
#
# .. _`inverted`: https://en.wikipedia.org/wiki/Invertible_matrix
