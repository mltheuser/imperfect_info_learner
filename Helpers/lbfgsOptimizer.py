import tensorflow as tf
import numpy as np
import tensorflow_probability as tfp

"""
Hier das grundsaätzliche vorgehen für einen einzigen schritt:

do bfgs on the model variables
compute the gradient from new - old variables

then return this gradient
"""


def function_factory(model, loss, train_x, train_y):
    """A factory to create a function required by tfp.optimizer.lbfgs_minimize.
    Args:
        model [in]: an instance of `tf.keras.Model` or its subclasses.
        loss [in]: a function with signature loss_value = loss(pred_y, true_y).
        train_x [in]: the input part of training data.
        train_y [in]: the output part of training data.
    Returns:
        A function that has a signature of:
            loss_value, gradients = f(model_parameters).
    """

    # obtain the shapes of all trainable parameters in the model
    shapes = tf.shape_n(model.trainable_variables)
    n_tensors = len(shapes)

    # we'll use tf.dynamic_stitch and tf.dynamic_partition later, so we need to
    # prepare required information first
    count = 0
    idx = []  # stitch indices
    part = []  # partition indices

    for i, shape in enumerate(shapes):
        n = np.product(shape)
        idx.append(tf.reshape(tf.range(count, count + n, dtype=tf.int32), shape))
        part.extend([i] * n)
        count += n

    part = tf.constant(part)

    @tf.function()
    def assign_new_model_parameters(params_1d):
        """A function updating the model's parameters with a 1D tf.Tensor.
        Args:
            params_1d [in]: a 1D tf.Tensor representing the model's trainable parameters.
        """

        params = tf.dynamic_partition(params_1d, part, n_tensors)
        for i, (shape, param) in enumerate(zip(shapes, params)):
            model.trainable_variables[i].assign(tf.reshape(param, shape))

    # now create a function that will be returned by this factory
    def f(params_1d):
        """A function that can be used by tfp.optimizer.lbfgs_minimize.
        This function is created by function_factory.
        Args:
           params_1d [in]: a 1D tf.Tensor.
        Returns:
            A scalar loss and the gradients w.r.t. the `params_1d`.
        """

        # use GradientTape so that we can calculate the gradient of loss w.r.t. parameters
        with tf.GradientTape() as tape:
            # update the parameters in the model
            assign_new_model_parameters(params_1d)
            # calculate the loss
            loss_value = loss(model, train_x, train_y)

        # calculate gradients and convert to 1D tf.Tensor
        grads = tape.gradient(loss_value, model.trainable_variables)
        grads = tf.dynamic_stitch(idx, grads)

        return loss_value, grads

    # store these information as members so we can use them outside the scope
    f.idx = idx
    f.part = part
    f.n_tensors = n_tensors
    f.shapes = shapes

    return f

def get_lbfg_gradient(model, loss_fun, train_x, train_y, param_cover_factor):
    func = function_factory(model, loss_fun, train_x, train_y)

    # convert initial model parameters to a 1D tf.Tensor
    init_params = tf.dynamic_stitch(func.idx, model.trainable_variables)

    # train the model with L-BFGS solver
    results = tfp.optimizer.lbfgs_minimize(
        value_and_gradients_function=func,
        initial_position=init_params,
        max_iterations=int(init_params.shape[0] * param_cover_factor),
        max_line_search_iterations=int(init_params.shape[0] * param_cover_factor)
    )

    # after training, the final optimized parameters are still in results.position
    # so we have to manually put them back to the model
    gradient_estimate = init_params - results.position

    params = tf.dynamic_partition(gradient_estimate, func.part, func.n_tensors)
    grads = []
    for i, (shape, param) in enumerate(zip(func.shapes, params)):
        grads.append(tf.reshape(param, shape))

    grads, _ = tf.clip_by_global_norm(
        grads,
        tf.linalg.global_norm(model.trainable_variables)
    )

    return grads
