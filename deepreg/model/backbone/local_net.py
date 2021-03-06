import tensorflow as tf

from deepreg.model import layer as layer


class LocalNet(tf.keras.Model):
    def __init__(self,
                 image_size, out_channels,
                 num_channel_initial, extract_levels,
                 out_kernel_initializer, out_activation,
                 **kwargs):
        """
        image is encoded gradually, i from level 0 to E
        then it is decoded gradually, j from level E to D
        some of the decoded level are used for generating extractions

        so extract_levels are between [0, E] with E = max(extract_levels) and D = min(extract_levels)

        :param out_channels: number of channels for the extractions
        :param num_channel_initial:
        :param extract_levels:
        :param out_kernel_initializer:
        :param out_activation:
        :param kwargs:
        """
        super(LocalNet, self).__init__(**kwargs)

        # save parameters
        self._extract_levels = extract_levels
        self._extract_max_level = max(self._extract_levels)  # E
        self._extract_min_level = min(self._extract_levels)  # D

        # init layer variables

        nc = [num_channel_initial * (2 ** level) for level in range(self._extract_max_level + 1)]  # level 0 to E
        self._downsample_blocks = [layer.DownSampleResnetBlock(filters=nc[i], kernel_size=7 if i == 0 else 3)
                                   for i in range(self._extract_max_level)]  # level 0 to E-1
        self._conv3d_block = layer.Conv3dBlock(filters=nc[-1])  # level E

        self._upsample_blocks = [layer.LocalNetUpSampleResnetBlock(nc[level]) for level in
                                 range(self._extract_max_level - 1, self._extract_min_level - 1, -1)]  # level D to E-1

        self._extract_layers = [
            # if kernels are not initialized by zeros, with init NN, extract may be too large
            layer.Conv3dWithResize(output_shape=image_size, filters=out_channels,
                                   kernel_initializer=out_kernel_initializer,
                                   activation=out_activation)
            for _ in self._extract_levels]

    def call(self, inputs, training=None, mask=None):
        """

        :param inputs: shape = [batch, f_dim1, f_dim2, f_dim3, ch]
        :param training:
        :param mask:
        :return:
        """

        # down sample from level 0 to E
        encoded = []  # outputs used for decoding, encoded[i] corresponds to level i, stored only 0 to E-1
        h = inputs
        for level in range(self._extract_max_level):  # level 0 to E - 1
            h, hc = self._downsample_blocks[level](inputs=h, training=training)
            encoded.append(hc)
        hm = self._conv3d_block(inputs=h, training=training)  # level E of encoding/decoding

        # up sample from level E to D
        decoded = [hm]  # level E
        for idx, level in enumerate(
                range(self._extract_max_level - 1, self._extract_min_level - 1, -1)):  # level E-1 to D
            hm = self._upsample_blocks[idx](inputs=[hm, encoded[level]], training=training)
            decoded.append(hm)

        # output
        output = tf.reduce_mean(tf.stack([self._extract_layers[idx](inputs=decoded[self._extract_max_level - level])
                                          for idx, level in enumerate(self._extract_levels)], axis=5), axis=5)
        return output
