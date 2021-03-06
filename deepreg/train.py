import os
from datetime import datetime

import click
import tensorflow as tf

import deepreg.config.parser as config_parser
import deepreg.data.load as load
import deepreg.model.loss.label as label_loss
import deepreg.model.metric as metric
import deepreg.model.network as network
import deepreg.model.optimizer as opt


@click.command()
@click.option(
    "--gpu", "-g",
    help="GPU index",
    type=str,
    required=True,
)
@click.option(
    "--config_path", "-c",
    help="Path of config",
    type=click.Path(file_okay=True, dir_okay=False, exists=True),
    required=True,
)
@click.option(
    "--gpu_allow_growth/--not_gpu_allow_growth",
    help="Do not take all GPU memory",
    default=False,
    show_default=True)
@click.option(
    "--ckpt_path",
    help="Path of checkpoint to load",
    default="",
    show_default=True,
    type=str,
)
@click.option(
    "--log",
    help="Name of log folder",
    default="",
    show_default=True,
    type=str,
)
def main(gpu, config_path, gpu_allow_growth, ckpt_path, log):
    # env vars
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu
    os.environ["TF_FORCE_GPU_ALLOW_GROWTH"] = "true" if gpu_allow_growth else "false"

    # load config
    config = config_parser.load(config_path)
    data_config = config["data"]
    tf_data_config = config["tf"]["data"]
    tf_opt_config = config["tf"]["opt"]
    tf_model_config = config["tf"]["model"]
    tf_loss_config = config["tf"]["loss"]
    num_epochs = config["tf"]["epochs"]
    save_period = config["tf"]["save_period"]
    histogram_freq = config["tf"]["histogram_freq"]
    log_dir = config["log_dir"][:-1] if config["log_dir"][-1] == "/" else config["log_dir"]

    # output
    log_folder_name = log if log != "" else datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = log_dir + "/" + log_folder_name

    checkpoint_init_path = ckpt_path
    if checkpoint_init_path != "":
        if not checkpoint_init_path.endswith(".ckpt"):
            raise ValueError("checkpoint path should end with .ckpt")

    # backup config
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    config_parser.save(config=config, out_dir=log_dir)

    # data
    data_loader_train = load.get_data_loader(data_config, "train")
    data_loader_val = load.get_data_loader(data_config, "valid")
    dataset_train = data_loader_train.get_dataset_and_preprocess(training=True, repeat=True, **tf_data_config)
    dataset_val = data_loader_val.get_dataset_and_preprocess(training=False, repeat=True, **tf_data_config)
    dataset_size_train = data_loader_train.num_images
    dataset_size_val = data_loader_val.num_images

    # optimizer
    optimizer = opt.get_optimizer(tf_opt_config)

    # callbacks
    tensorboard_callback = tf.keras.callbacks.TensorBoard(log_dir=log_dir, histogram_freq=histogram_freq)
    checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
        filepath=log_dir + "/save/weights-epoch{epoch:d}.ckpt", save_weights_only=True,
        period=save_period)

    strategy = tf.distribute.MirroredStrategy()
    with strategy.scope():
        # model
        model = network.build_model(moving_image_size=data_loader_train.moving_image_shape,
                                    fixed_image_size=data_loader_train.fixed_image_shape,
                                    index_size=data_loader_train.num_indices,
                                    batch_size=tf_data_config["batch_size"],
                                    tf_model_config=tf_model_config,
                                    tf_loss_config=tf_loss_config)
        model.summary()
        # metrics
        model.compile(optimizer=optimizer,
                      loss=label_loss.get_similarity_fn(config=tf_loss_config["similarity"]["label"]),
                      metrics=[metric.MeanDiceScore(),
                               metric.MeanCentroidDistance(grid_size=data_loader_train.fixed_image_shape),
                               metric.MeanForegroundProportion(pred=False),
                               metric.MeanForegroundProportion(pred=True),
                               ])
        print(model.summary())

        # load weights
        if checkpoint_init_path != "":
            model.load_weights(checkpoint_init_path)

        # train
        # it's necessary to define the steps_per_epoch and validation_steps to prevent errors like
        # BaseCollectiveExecutor::StartAbort Out of range: End of sequence
        model.fit(
            x=dataset_train,
            steps_per_epoch=dataset_size_train // tf_data_config["batch_size"],
            epochs=num_epochs,
            validation_data=dataset_val,
            validation_steps=dataset_size_val // tf_data_config["batch_size"],
            callbacks=[tensorboard_callback, checkpoint_callback],
        )


if __name__ == "__main__":
    main()
