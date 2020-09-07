# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""FFCC Training and Evaluation."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
from ffcc import model
from ffcc import ops
from ffcc import io
from ffcc import input as ffcc_input
import os
import json
from tensorboard.plugins.hparams import api as hp
import tensorflow as tf


os.environ['CUDA_VISIBLE_DEVICES'] = '-1'


# This directory stores the images and metadata for training.
DATA_DIR = '../data/shi_gehler/preprocessed/GehlerShi'

# Project name
PROJECT_NAME = 'GehlerShi'

# Model dir for checkpoints, etc.
MODEL_DIR = './GehlerShi_model/'

# The number of epochs for training.
NUM_EPOCHS = 150

# Number of testing fold in the 3-fold cross validation
TEST_FOLD = 1 # if TEST_FOLD=0, the code will train three models, each of
# which  will be traind with two different folds.

# How often should checkpoints and summaries be saved and computed, for each
# training epoch. Higher numbers will result in more frequent tensorboard
# updates, at the expense of training efficiency.
NUM_UPDATES_PER_EPOCH = 10

# To print the eval results on the latest checkpoint.
do_print_eval = False


def train_and_eval_fn(params, hparams, train_set, train_label, eval_set,
                      eval_label, model_dir):
  """Creates A tf.estimator and the corresponding training and eval specs.

  Args:
    params: dictionary of model parameters.
    hparams: dictionary of hyperparameters.
    train_set: A list of training data dictionaries generated by
    'io.read_dataset_from_files'.
    eval_set: A list of evaluation data dictionaries generated by
    'io.read_dataset_from_files'.
    model_dir: Name of model dir to store checkpoints.

  Returns:
    A tuple (estimator, train_spec, eval_spec).
  """

  tf.compat.v1.logging.info('hparams = %s', hparams)
  tf.compat.v1.logging.info('num_epochs = %s', NUM_EPOCHS)

  (train_input_fn, total_training_iterations) = (
      ffcc_input.input_builder_stratified(train_set, train_label,
                                          hparams['batch_size'], NUM_EPOCHS))
  (eval_input_fn, _) = (
    ffcc_input.input_builder_stratified(eval_set, eval_label, batch_size=1,
                                        num_epochs=1))

  update_steps = max(
      1, math.ceil(total_training_iterations / NUM_UPDATES_PER_EPOCH))
  run_config = tf.estimator.RunConfig(
      save_checkpoints_steps=update_steps, save_summary_steps=update_steps)

  # Note: the max_steps need to be set otherwise the eval job on borg will be
  # stuck in infinite loop: b/130740041
  train_spec = tf.estimator.TrainSpec(
      input_fn=train_input_fn, max_steps=total_training_iterations)

  # Just to be sure that evaluation is working, we manually force evaluation
  # for all batches in the eval set. Thresholds are set to small values so that
  # evaluation happens for all checkpoints as per `save_checkpoints_steps`.
  eval_steps = 10

  # Training the FFCC model is really fast; we set delays to 0s in order to
  # make intermediate evaluation results visible in tensorboard.
  eval_spec = tf.estimator.EvalSpec(
      name='default',
      input_fn=eval_input_fn,
      steps=eval_steps,
      start_delay_secs=0,
      throttle_secs=0)

  # Create the tf.Estimator instance
  estimator = tf.estimator.Estimator(
      model_dir=model_dir,
      model_fn=model.model_builder(hparams),
      params=params,
      config=run_config)
  return estimator, train_spec, eval_spec


def print_eval(params, hparams, model_dir, eval_set):
  """Printing the eval results on the latest checkpoint.

  This function will run on the entired TFRecord dataset and it is useful for
  validating the training result.

  Args:
    params: model params
    hparams: a tf.HParams object created by create_default_hparams().
    model_dir: Name of model dir to store checkpoints.
    eval_set: A list of evaluation data dictionaries generated by
      'io.read_dataset_from_files'.
  """
  (_, eval_input_fn, _) = \
      ffcc_input.input_builder_stratified(eval_set, batch_size=1,
                                          num_epochs=1)

  run_config = tf.estimator.RunConfig()
  estimator = tf.estimator.Estimator(
      model_fn=model.model_builder(hparams),
      params=params,
      model_dir=model_dir,
      config=run_config)

  for result in estimator.predict(input_fn=eval_input_fn):
    burst_id = result['burst_id']
    uv = result['uv']
    # Convert UV to RGB illuminants for visualization
    with tf.Graph().as_default():
      with tf.compat.v1.Session() as sess:
        uv_batch = uv.reshape((1, 2))
        rgb = sess.run(ops.uv_to_rgb(uv_batch))[0]
    tf.compat.v1.logging.info('id=%s uv=%s rgb=%s', burst_id.decode('utf-8'),
                              uv, rgb)


def main(_):
  # Something about this code causes optimization to perform very poorly when
  # run on a GPU, so as a safeguard we prevent the user from using CUDA.
  # TODO(barron/yuntatsai): Track down the source of this discrepancy (maybe
  # something involving the FFT gradients?).

  assert not tf.test.is_built_with_cuda()

  if tf.io.gfile.exists(PROJECT_NAME + '_hyperparams.json'):
    print('Loading hyperparameters from %s_hyperparams.json' % PROJECT_NAME)
    with open(PROJECT_NAME + '_hyperparams.json', 'r') as hparams_file:
      hparams = json.load(hparams_file)
  else:
    # TODO(fbleibel): populate params and hparams based on project files.
    hparams = []
    with open(PROJECT_NAME + '_hyperparams.json', 'w') as fp:
      json.dump(hparams, fp)

  if tf.io.gfile.exists(PROJECT_NAME + '_params.json'):
    print('Loading hyperparameters from %s_params.json' % PROJECT_NAME)
    with open(PROJECT_NAME + '_params.json', 'r') as params_file:
      params = json.load(params_file)
  else:
    params = {'first_bin': -0.4375,
              'nbins': 64,
              'bin_size': 0.03125,
              'variance': 0.0833333333,
              'ellipse_params': {
                'w_mat': [2.2869704, 0.7841647, 0.7841649, 1.3999774],
                 'b_vec': [-1.2658409, -1.2520925]
              },
              'extended_feature_bins': [0.0],
              'extended_vector_length': 1}
    with open(PROJECT_NAME + '_params.json', 'w') as fp:
      json.dump(params, fp)


  if TEST_FOLD == 0:
    for test_fold in range(1, 4):
      # create sub-directory to stores checkpoints for the current testing fold
      current_model_dir = os.path.join(MODEL_DIR, 'fold%d' % test_fold)
      if not tf.io.gfile.exists(current_model_dir):
        tf.io.gfile.makedirs(current_model_dir)

      print('Reading data from %s ....\n' % DATA_DIR)
      train_set, train_labels, eval_set, eval_labels = \
        io.read_dataset_from_dir(DATA_DIR, test_fold)

      estimator, train_spec, eval_spec = train_and_eval_fn(
        params, hparams, train_set, train_labels, eval_set, eval_labels)
      if do_print_eval:
        print_eval(params, hparams)
      else:
        tf.estimator.train_and_evaluate(estimator, train_spec, eval_spec)
  else:
    # create sub-directory to stores checkpoints for the current testing fold
    current_model_dir = os.path.join(MODEL_DIR, 'fold%d' % TEST_FOLD)
    if not tf.io.gfile.exists(current_model_dir):
      tf.io.gfile.makedirs(current_model_dir)

    print('Reading data from %s ....\n' % DATA_DIR)
    train_set, train_label, eval_set, eval_label = io.read_dataset_from_dir(
      DATA_DIR, TEST_FOLD)

    estimator, train_spec, eval_spec = train_and_eval_fn(
      params, hparams, train_set, train_label, eval_set, eval_label,
      current_model_dir)
    if do_print_eval:
      print_eval(params, hparams)
    else:
      tf.estimator.train_and_evaluate(estimator, train_spec, eval_spec)


if __name__ == '__main__':
  tf.compat.v1.disable_eager_execution()
  tf.compat.v1.logging.set_verbosity(tf.compat.v1.logging.INFO)
  tf.compat.v1.app.run(main)
