"""
Utility functions of matrix and vector transformations.

NOTE: convention for quaternions is (x, y, z, w)
"""

import math
from typing import Optional, Tuple, Union, List

import torch as th

PI = math.pi
EPS = th.finfo(th.float32).eps * 4.0

# axis sequences for Euler angles
_NEXT_AXIS = [1, 2, 0, 1]

# map axes strings to/from tuples of inner axis, parity, repetition, frame
_AXES2TUPLE = {
    "sxyz": (0, 0, 0, 0),
    "sxyx": (0, 0, 1, 0),
    "sxzy": (0, 1, 0, 0),
    "sxzx": (0, 1, 1, 0),
    "syzx": (1, 0, 0, 0),
    "syzy": (1, 0, 1, 0),
    "syxz": (1, 1, 0, 0),
    "syxy": (1, 1, 1, 0),
    "szxy": (2, 0, 0, 0),
    "szxz": (2, 0, 1, 0),
    "szyx": (2, 1, 0, 0),
    "szyz": (2, 1, 1, 0),
    "rzyx": (0, 0, 0, 1),
    "rxyx": (0, 0, 1, 1),
    "ryzx": (0, 1, 0, 1),
    "rxzx": (0, 1, 1, 1),
    "rxzy": (1, 0, 0, 1),
    "ryzy": (1, 0, 1, 1),
    "rzxy": (1, 1, 0, 1),
    "ryxy": (1, 1, 1, 1),
    "ryxz": (2, 0, 0, 1),
    "rzxz": (2, 0, 1, 1),
    "rxyz": (2, 1, 0, 1),
    "rzyz": (2, 1, 1, 1),
}

_TUPLE2AXES = dict((v, k) for k, v in _AXES2TUPLE.items())


@th.jit.script
def copysign(a, b):
    # type: (float, Tensor) -> Tensor
    a = th.tensor(a, device=b.device, dtype=th.float).repeat(b.shape[0])
    return th.abs(a) * th.sign(b)


@th.jit.script
def anorm(x: th.Tensor, dim: Optional[int] = None, keepdim: bool = False) -> th.Tensor:
    """Compute L2 norms along specified axes."""
    return th.norm(x, dim=dim, keepdim=keepdim)


@th.jit.script
def normalize(v: th.Tensor, dim: Optional[int] = None, eps: float = 1e-10) -> th.Tensor:
    """L2 Normalize along specified axes."""
    norm = anorm(v, dim=dim, keepdim=True)
    return v / th.where(norm < eps, th.full_like(norm, eps), norm)


@th.jit.script
def dot(v1, v2, dim=-1, keepdim=False):
    """
    Computes dot product between two vectors along the provided dim, optionally keeping the dimension

    Args:
        v1 (tensor): (..., N, ...) arbitrary vector
        v2 (tensor): (..., N, ...) arbitrary vector
        dim (int): Dimension to sum over for dot product
        keepdim (bool): Whether to keep dimension over which dot product is calculated

    Returns:
        tensor: (..., [1,] ...) dot product of vectors, with optional dimension kept if @keepdim is True
    """
    # type: (Tensor, Tensor, int, bool) -> Tensor
    return th.sum(v1 * v2, dim=dim, keepdim=keepdim)


@th.jit.script
def quat_mul(a, b):
    assert a.shape == b.shape
    shape = a.shape
    a = a.reshape(-1, 4)
    b = b.reshape(-1, 4)

    x1, y1, z1, w1 = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    x2, y2, z2, w2 = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    ww = (z1 + x1) * (x2 + y2)
    yy = (w1 - y1) * (w2 + z2)
    zz = (w1 + y1) * (w2 - z2)
    xx = ww + yy + zz
    qq = 0.5 * (xx + (z1 - x1) * (x2 - y2))
    w = qq - ww + (z1 - y1) * (y2 - z2)
    x = qq - xx + (x1 + w1) * (x2 + w2)
    y = qq - yy + (w1 - x1) * (y2 + z2)
    z = qq - zz + (z1 + y1) * (w2 - x2)

    quat = th.stack([x, y, z, w], dim=-1).view(shape)

    return quat


@th.jit.script
def unit_vector(data: th.Tensor, dim: Optional[int] = None, out: Optional[th.Tensor] = None) -> th.Tensor:
    """
    Returns tensor normalized by length, i.e. Euclidean norm, along axis.

    Args:
        data (th.Tensor): data to normalize
        dim (Optional[int]): If specified, determines specific dimension along data to normalize
        out (Optional[th.Tensor]): If specified, will store computation in this variable

    Returns:
        th.Tensor: Normalized vector
    """
    if out is None:
        if not isinstance(data, th.Tensor):
            data = th.tensor(data, dtype=th.float32)
        else:
            data = data.clone().to(th.float32)

        if data.ndim == 1:
            return data / th.sqrt(th.dot(data, data))
    else:
        if out is not data:
            out.copy_(data)
        data = out

    if dim is None:
        dim = -1

    length = th.sum(data * data, dim=dim, keepdim=True).sqrt()
    data = data / (length + 1e-8)  # Add small epsilon to avoid division by zero

    return data


@th.jit.script
def quat_apply(quat: th.Tensor, vec: th.Tensor) -> th.Tensor:
    """
    Apply a quaternion rotation to a vector (equivalent to R.from_quat(x).apply(y))

    Args:
        quat (th.Tensor): (..., 4) quaternion in (x, y, z, w) format
        vec (th.Tensor): (..., 3) vector to rotate

    Returns:
        th.Tensor: (..., 3) rotated vector

    Raises:
        AssertionError: If input shapes are invalid
    """
    assert quat.shape[-1] == 4, "Quaternion must have 4 components in last dimension"
    assert vec.shape[-1] == 3, "Vector must have 3 components in last dimension"

    # Extract quaternion components
    qx, qy, qz, qw = quat.unbind(-1)

    # Compute the quaternion multiplication
    t = th.stack(
        [
            2 * (qy * vec[..., 2] - qz * vec[..., 1]),
            2 * (qz * vec[..., 0] - qx * vec[..., 2]),
            2 * (qx * vec[..., 1] - qy * vec[..., 0]),
        ],
        dim=-1,
    )

    # Compute the final rotated vector
    return vec + qw.unsqueeze(-1) * t + th.cross(quat[..., :3], t, dim=-1)


@th.jit.script
def ewma_vectorized(
    data: th.Tensor, alpha: float, offset: Optional[float] = None, dtype: Optional[str] = None, order: str = "C"
) -> th.Tensor:
    """
    Calculates the exponential moving average over a vector.
    Will fail for large inputs.

    Args:
        data (th.Tensor): Input data
        alpha (float): scalar in range (0,1)
            The alpha parameter for the moving average.
        offset (Optional[float]): If specified, the offset for the moving average. None defaults to data[0].
        dtype (Optional[str]): Data type used for calculations. If None, defaults to float64 unless
            data.dtype is float32, then it will use float32. Valid options are 'float32' and 'float64'.
        order (str): Order to use when flattening the data. Valid options are {'C', 'F', 'A'}.

    Returns:
        th.Tensor: Exponential moving average from @data
    """
    if dtype is None:
        dtype = "float32" if data.dtype == th.float32 else "float64"

    if dtype == "float32":
        data = data.to(th.float32)
    else:
        data = data.to(th.float64)

    if data.ndim > 1:
        # flatten input
        data = data.reshape(-1)

    out = th.empty_like(data)

    if data.size(0) < 1:
        # empty input, return empty array
        return out

    if offset is None:
        offset = data[0].item()

    alpha = th.tensor(alpha, dtype=data.dtype)

    # scaling_factors -> 0 as len(data) gets large
    # this leads to divide-by-zeros below
    scaling_factors = th.pow(1.0 - alpha, th.arange(data.size(0) + 1, dtype=data.dtype))
    # create cumulative sum array
    out = data * (alpha * scaling_factors[-2]) / scaling_factors[:-1]
    out = th.cumsum(out, dim=0)

    # cumsums / scaling
    out = out / scaling_factors[-2::-1]

    if offset != 0:
        offset = th.tensor(offset, dtype=data.dtype)
        # add offsets
        out = out + offset * scaling_factors[1:]

    return out


@th.jit.script
def convert_quat(q: th.Tensor, to: str = "xyzw") -> th.Tensor:
    """
    Converts quaternion from one convention to another.
    The convention to convert TO is specified as an optional argument.
    If to == 'xyzw', then the input is in 'wxyz' format, and vice-versa.

    Args:
        q (th.Tensor): a 4-dim array corresponding to a quaternion
        to (str): either 'xyzw' or 'wxyz', determining which convention to convert to.

    Returns:
        th.Tensor: The converted quaternion
    """
    if to == "xyzw":
        return th.stack([q[1], q[2], q[3], q[0]], dim=0)
    elif to == "wxyz":
        return th.stack([q[3], q[0], q[1], q[2]], dim=0)
    else:
        raise ValueError("convert_quat: choose a valid `to` argument (xyzw or wxyz)")


@th.jit.script
def quat_multiply(quaternion1: th.Tensor, quaternion0: th.Tensor) -> th.Tensor:
    """
    Return multiplication of two quaternions (q1 * q0).

    Args:
        quaternion1 (th.Tensor): (x,y,z,w) quaternion
        quaternion0 (th.Tensor): (x,y,z,w) quaternion

    Returns:
        th.Tensor: (x,y,z,w) multiplied quaternion
    """
    x0, y0, z0, w0 = quaternion0[0], quaternion0[1], quaternion0[2], quaternion0[3]
    x1, y1, z1, w1 = quaternion1[0], quaternion1[1], quaternion1[2], quaternion1[3]

    return th.stack(
        [
            x1 * w0 + y1 * z0 - z1 * y0 + w1 * x0,
            -x1 * z0 + y1 * w0 + z1 * x0 + w1 * y0,
            x1 * y0 - y1 * x0 + z1 * w0 + w1 * z0,
            -x1 * x0 - y1 * y0 - z1 * z0 + w1 * w0,
        ],
        dim=0,
    )


@th.jit.script
def quat_conjugate(quaternion):
    """
    Return conjugate of quaternion.

    E.g.:
    >>> q0 = random_quaternion()
    >>> q1 = quat_conjugate(q0)
    >>> q1[3] == q0[3] and all(q1[:3] == -q0[:3])
    True

    Args:
        quaternion (th.tensor): (x,y,z,w) quaternion

    Returns:
        th.tensor: (x,y,z,w) quaternion conjugate
    """
    return th.tensor(
        (-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3]),
        dtype=th.float32,
    )


@th.jit.script
def quat_inverse(quaternion):
    """
    Return inverse of quaternion.

    E.g.:
    >>> q0 = random_quaternion()
    >>> q1 = quat_inverse(q0)
    >>> th.allclose(quat_multiply(q0, q1), [0, 0, 0, 1])
    True

    Args:
        quaternion (th.tensor): (x,y,z,w) quaternion

    Returns:
        th.tensor: (x,y,z,w) quaternion inverse
    """
    return quat_conjugate(quaternion) / th.dot(quaternion, quaternion)


@th.jit.script
def quat_distance(quaternion1, quaternion0):
    """
    Returns distance between two quaternions, such that distance * quaternion0 = quaternion1

    Args:
        quaternion1 (th.tensor): (x,y,z,w) quaternion
        quaternion0 (th.tensor): (x,y,z,w) quaternion

    Returns:
        th.tensor: (x,y,z,w) quaternion distance
    """
    return quat_multiply(quaternion1, quat_inverse(quaternion0))


@th.jit.script
def quat_slerp(quat0, quat1, frac, shortestpath=True, eps=1.0e-15):
    """
    Return spherical linear interpolation between two quaternions.

    Args:
        quat0 (tensor): (..., 4) tensor where the final dim is (x,y,z,w) initial quaternion
        quat1 (tensor): (..., 4) tensor where the final dim is (x,y,z,w) final quaternion
        frac (tensor): Values in [0.0, 1.0] representing fraction of interpolation
        shortestpath (bool): If True, will calculate shortest path
        eps (float): Value to check for singularities
    Returns:
        tensor: (..., 4) Interpolated
    """
    # type: (Tensor, Tensor, Tensor, bool, float) -> Tensor
    # reshape quaternion
    quat_shape = quat0.shape
    quat0 = unit_vector(quat0.reshape(-1, 4), dim=-1)
    quat1 = unit_vector(quat1.reshape(-1, 4), dim=-1)

    # Check for endpoint cases
    where_start = frac <= 0.0
    where_end = frac >= 1.0

    d = dot(quat0, quat1, dim=-1, keepdim=True)
    if shortestpath:
        quat1 = th.where(d < 0.0, -quat1, quat1)
        d = th.abs(d)
    angle = th.acos(th.clip(d, -1.0, 1.0))

    # Check for small quantities (i.e.: q0 = q1)
    where_small_diff = th.abs(th.abs(d) - 1.0) < eps
    where_small_angle = abs(angle) < eps

    isin = 1.0 / th.sin(angle)
    val = quat0 * th.sin((1.0 - frac) * angle) * isin + quat1 * th.sin(frac * angle) * isin

    # Filter edge cases
    val = th.where(
        where_small_diff | where_small_angle | where_start,
        quat0,
        th.where(
            where_end,
            quat1,
            val,
        ),
    )

    # Reshape and return values
    return val.reshape(list(quat_shape))


@th.jit.script
def random_quat(rand=None):
    """
    Return uniform random unit quaternion.

    E.g.:
    >>> q = random_quat()
    >>> th.allclose(1.0, vector_norm(q))
    True
    >>> q = random_quat(th.rand(3))
    >>> q.shape
    (4,)

    Args:
        rand (3-array or None): If specified, must be three independent random variables that are uniformly distributed
            between 0 and 1.

    Returns:
        th.tensor: (x,y,z,w) random quaternion
    """
    if rand is None:
        rand = th.rand(3)
    else:
        assert len(rand) == 3
    r1 = math.sqrt(1.0 - rand[0])
    r2 = math.sqrt(rand[0])
    pi2 = math.pi * 2.0
    t1 = pi2 * rand[1]
    t2 = pi2 * rand[2]
    return th.tensor(
        (th.sin(t1) * r1, th.cos(t1) * r1, th.sin(t2) * r2, th.cos(t2) * r2),
        dtype=th.float32,
    )


@th.jit.script
def random_axis_angle(angle_limit=None, random_state=None):
    """
    Samples an axis-angle rotation by first sampling a random axis
    and then sampling an angle. If @angle_limit is provided, the size
    of the rotation angle is constrained.

    If @random_state is provided (instance of th.Generator), it
    will be used to generate random numbers.

    Args:
        angle_limit (None or float): If set, determines magnitude limit of angles to generate
        random_state (None or th.Generator): RNG to use if specified

    Raises:
        AssertionError: [Invalid RNG]
    """
    if angle_limit is None:
        angle_limit = 2.0 * math.pi

    if random_state is not None:
        assert isinstance(random_state, th.Generator)
        generator = random_state
    else:
        generator = None

    # sample random axis using a normalized sample from spherical Gaussian.
    # see (http://extremelearning.com.au/how-to-generate-uniformly-random-points-on-n-spheres-and-n-balls/)
    # for why it works.
    random_axis = th.randn(3, generator=generator)
    random_axis /= th.norm(random_axis)
    random_angle = th.rand(1, generator=generator) * angle_limit
    return random_axis, random_angle.item()


@th.jit.script
def vec(values):
    """
    Converts value tuple into a numpy vector.

    Args:
        values (n-array): a tuple of numbers

    Returns:
        th.tensor: vector of given values
    """
    return th.tensor(values, dtype=th.float32)


@th.jit.script
def mat4(tensor):
    """
    Converts an tensor to 4x4 matrix.

    Args:
        tensor (n-tensor): the tensor in form of vec, list, or tuple

    Returns:
        th.tensor: a 4x4 th tensor
    """
    return th.tensor(tensor, dtype=th.float32).view((4, 4))


@th.jit.script
def quat2mat(quaternion):
    """
    Converts given quaternion to matrix.
    Args:
        quaternion (tensor): (..., 4) tensor where the final dim is (x,y,z,w) quaternion
    Returns:
        tensor: (..., 3, 3) tensor whose final two dimensions are 3x3 rotation matrices
    """
    # convert quat convention
    inds = th.tensor([3, 0, 1, 2])
    input_shape = quaternion.shape[:-1]
    q = quaternion.reshape(-1, 4)[:, inds]
    # Conduct dot product
    n = th.bmm(q.unsqueeze(1), q.unsqueeze(-1)).squeeze(-1).squeeze(-1)  # shape (-1)
    idx = th.nonzero(n).reshape(-1)
    q_ = q.clone()  # Copy so we don't have inplace operations that fail to backprop
    q_[idx, :] = q[idx, :] * th.sqrt(2.0 / n[idx].unsqueeze(-1))
    # Conduct outer product
    q2 = th.bmm(q_.unsqueeze(-1), q_.unsqueeze(1)).squeeze(-1).squeeze(-1)  # shape (-1, 4 ,4)
    # Create return array
    ret = (
        th.eye(3, 3, dtype=quaternion.dtype, device=q.device)
        .reshape(1, 3, 3)
        .repeat(th.prod(th.tensor(input_shape)), 1, 1)
    )
    ret[idx, :, :] = th.stack(
        [
            th.stack(
                [1.0 - q2[idx, 2, 2] - q2[idx, 3, 3], q2[idx, 1, 2] - q2[idx, 3, 0], q2[idx, 1, 3] + q2[idx, 2, 0]],
                dim=-1,
            ),
            th.stack(
                [q2[idx, 1, 2] + q2[idx, 3, 0], 1.0 - q2[idx, 1, 1] - q2[idx, 3, 3], q2[idx, 2, 3] - q2[idx, 1, 0]],
                dim=-1,
            ),
            th.stack(
                [q2[idx, 1, 3] - q2[idx, 2, 0], q2[idx, 2, 3] + q2[idx, 1, 0], 1.0 - q2[idx, 1, 1] - q2[idx, 2, 2]],
                dim=-1,
            ),
        ],
        dim=1,
    ).to(dtype=quaternion.dtype)
    # Reshape and return output
    ret = ret.reshape(list(input_shape) + [3, 3])
    return ret


@th.jit.script
def mat2quat(rmat: th.Tensor) -> th.Tensor:
    """
    Converts given rotation matrix to quaternion.
    Args:
        rmat (th.Tensor): (..., 3, 3) rotation matrix
    Returns:
        th.Tensor: (..., 4) (x,y,z,w) float quaternion angles
    """
    # Ensure the input is at least 3D
    original_shape = rmat.shape
    if rmat.dim() < 3:
        rmat = rmat.unsqueeze(0)

    # Check if the matrix is close to identity
    identity = th.eye(3, device=rmat.device).expand_as(rmat)
    if th.allclose(rmat, identity, atol=1e-6):
        quat = th.zeros_like(rmat[..., 0])  # Creates a tensor with shape (..., 3)
        quat = th.cat([quat, th.ones_like(quat[..., :1])], dim=-1)  # Adds the w component
    else:
        m00, m01, m02 = rmat[..., 0, 0], rmat[..., 0, 1], rmat[..., 0, 2]
        m10, m11, m12 = rmat[..., 1, 0], rmat[..., 1, 1], rmat[..., 1, 2]
        m20, m21, m22 = rmat[..., 2, 0], rmat[..., 2, 1], rmat[..., 2, 2]

        trace = m00 + m11 + m22

        if trace > 0:
            s = 2.0 * th.sqrt(trace + 1.0)
            w = 0.25 * s
            x = (m21 - m12) / s
            y = (m02 - m20) / s
            z = (m10 - m01) / s
        elif m00 > m11 and m00 > m22:
            s = 2.0 * th.sqrt(1.0 + m00 - m11 - m22)
            w = (m21 - m12) / s
            x = 0.25 * s
            y = (m01 + m10) / s
            z = (m02 + m20) / s
        elif m11 > m22:
            s = 2.0 * th.sqrt(1.0 + m11 - m00 - m22)
            w = (m02 - m20) / s
            x = (m01 + m10) / s
            y = 0.25 * s
            z = (m12 + m21) / s
        else:
            s = 2.0 * th.sqrt(1.0 + m22 - m00 - m11)
            w = (m10 - m01) / s
            x = (m02 + m20) / s
            y = (m12 + m21) / s
            z = 0.25 * s

        quat = th.stack([x, y, z, w], dim=-1)

    # Normalize the quaternion
    quat = quat / th.norm(quat, dim=-1, keepdim=True)

    # Remove extra dimensions if they were added
    if len(original_shape) == 2:
        quat = quat.squeeze(0)

    return quat


@th.jit.script
def mat2pose(hmat):
    """
    Converts a homogeneous 4x4 matrix into pose.

    Args:
        hmat (th.tensor): a 4x4 homogeneous matrix

    Returns:
        2-tuple:
            - (th.tensor) (x,y,z) position array in cartesian coordinates
            - (th.tensor) (x,y,z,w) orientation array in quaternion form
    """
    pos = hmat[:3, 3]
    orn = mat2quat(hmat[:3, :3])
    return pos, orn


@th.jit.script
def vec2quat(vec: th.Tensor, up: th.Tensor = th.tensor([0.0, 0.0, 1.0])) -> th.Tensor:
    """
    Converts given 3d-direction vector @vec to quaternion orientation with respect to another direction vector @up

    Args:
        vec (th.Tensor): (x,y,z) direction vector (possibly non-normalized)
        up (th.Tensor): (x,y,z) direction vector representing the canonical up direction (possibly non-normalized)

    Returns:
        th.Tensor: (x,y,z,w) quaternion
    """
    # Ensure inputs are 2D
    if vec.dim() == 1:
        vec = vec.unsqueeze(0)
    if up.dim() == 1:
        up = up.unsqueeze(0)

    vec_n = th.nn.functional.normalize(vec, dim=-1)
    up_n = th.nn.functional.normalize(up, dim=-1)

    s_n = th.cross(up_n, vec_n, dim=-1)
    u_n = th.cross(vec_n, s_n, dim=-1)

    rotation_matrix = th.stack([vec_n, s_n, u_n], dim=-1)

    return mat2quat(rotation_matrix)


@th.jit.script
def euler2quat(euler: th.Tensor) -> th.Tensor:
    """
    Converts euler angles into quaternion form

    Args:
        euler (th.Tensor): (..., 3) (r,p,y) angles

    Returns:
        th.Tensor: (..., 4) (x,y,z,w) float quaternion angles

    Raises:
        AssertionError: [Invalid input shape]
    """
    assert euler.shape[-1] == 3, "Invalid input shape"

    # Unpack roll, pitch, yaw
    roll, pitch, yaw = euler.unbind(-1)

    # Compute sines and cosines of half angles
    cy = th.cos(yaw * 0.5)
    sy = th.sin(yaw * 0.5)
    cr = th.cos(roll * 0.5)
    sr = th.sin(roll * 0.5)
    cp = th.cos(pitch * 0.5)
    sp = th.sin(pitch * 0.5)

    # Compute quaternion components
    qw = cy * cr * cp + sy * sr * sp
    qx = cy * sr * cp - sy * cr * sp
    qy = cy * cr * sp + sy * sr * cp
    qz = sy * cr * cp - cy * sr * sp

    # Stack and return
    return th.stack([qx, qy, qz, qw], dim=-1)


@th.jit.script
def quat2euler(q):
    """
    Converts euler angles into quaternion form

    Args:
        quat (th.tensor): (x,y,z,w) float quaternion angles

    Returns:
        th.tensor: (r,p,y) angles

    Raises:
        AssertionError: [Invalid input shape]
    """
    qx, qy, qz, qw = 0, 1, 2, 3
    # roll (x-axis rotation)
    sinr_cosp = 2.0 * (q[:, qw] * q[:, qx] + q[:, qy] * q[:, qz])
    cosr_cosp = q[:, qw] * q[:, qw] - q[:, qx] * q[:, qx] - q[:, qy] * q[:, qy] + q[:, qz] * q[:, qz]
    roll = th.atan2(sinr_cosp, cosr_cosp)

    # pitch (y-axis rotation)
    sinp = 2.0 * (q[:, qw] * q[:, qy] - q[:, qz] * q[:, qx])
    pitch = th.where(th.abs(sinp) >= 1, copysign(math.pi / 2.0, sinp), th.asin(sinp))

    # yaw (z-axis rotation)
    siny_cosp = 2.0 * (q[:, qw] * q[:, qz] + q[:, qx] * q[:, qy])
    cosy_cosp = q[:, qw] * q[:, qw] + q[:, qx] * q[:, qx] - q[:, qy] * q[:, qy] - q[:, qz] * q[:, qz]
    yaw = th.atan2(siny_cosp, cosy_cosp)

    return roll % (2 * math.pi), pitch % (2 * math.pi), yaw % (2 * math.pi)


@th.jit.script
def euler2mat(euler):
    """
    Converts euler angles into rotation matrix form

    Args:
        euler (th.tensor): (r,p,y) angles

    Returns:
        th.tensor: 3x3 rotation matrix

    Raises:
        AssertionError: [Invalid input shape]
    """
    euler = th.as_tensor(euler, dtype=th.float32)
    assert euler.shape[-1] == 3, f"Invalid shaped euler {euler}"

    # Convert Euler angles to quaternion
    quat = euler2quat(euler)

    # Convert quaternion to rotation matrix
    return quat2mat(quat)


@th.jit.script
def mat2euler(rmat):
    """
    Converts given rotation matrix to euler angles in radian.

    Args:
        rmat (th.tensor): 3x3 rotation matrix

    Returns:
        th.tensor: (r,p,y) converted euler angles in radian vec3 float
    """
    M = th.as_tensor(rmat, dtype=th.float32)[:3, :3]

    # Convert rotation matrix to quaternion
    # Note: You'll need to implement mat2quat function
    quat = mat2quat(M)

    # Convert quaternion to Euler angles
    roll, pitch, yaw = quat2euler(quat)

    return th.stack([roll, pitch, yaw], dim=-1)


@th.jit.script
def pose2mat(pose: Tuple[th.Tensor, th.Tensor]) -> th.Tensor:
    """
    Converts pose to homogeneous matrix.

    Args:
        pose (Tuple[th.Tensor, th.Tensor]): a (pos, orn) tuple where pos is vec3 float cartesian,
            and orn is vec4 float quaternion.

    Returns:
        th.Tensor: 4x4 homogeneous matrix
    """
    pos, orn = pose
    homo_pose_mat = th.eye(4, dtype=th.float32)
    homo_pose_mat[:3, :3] = quat2mat(orn)
    homo_pose_mat[:3, 3] = pos.float()
    return homo_pose_mat


@th.jit.script
def quat2axisangle(quat):
    """
    Converts quaternion to axis-angle format.
    Returns a unit vector direction scaled by its angle in radians.
    Args:
        quat (tensor): (..., 4) tensor where the final dim is (x,y,z,w) quaternion
    Returns:
        tensor: (..., 3) axis-angle exponential coordinates
    """
    # reshape quaternion
    quat_shape = quat.shape[:-1]  # ignore last dim
    quat = quat.reshape(-1, 4)
    # clip quaternion
    quat[:, 3] = th.clamp(quat[:, 3], -1.0, 1.0)
    # Calculate denominator
    den = th.sqrt(1.0 - quat[:, 3] * quat[:, 3])
    # Map this into a mask

    # Create return array
    ret = th.zeros_like(quat)[:, :3]
    idx = th.nonzero(den).reshape(-1)
    ret[idx, :] = (quat[idx, :3] * 2.0 * th.acos(quat[idx, 3]).unsqueeze(-1)) / den[idx].unsqueeze(-1)

    # Reshape and return output
    ret = ret.reshape(
        list(quat_shape)
        + [
            3,
        ]
    )
    return ret


@th.jit.script
def axisangle2quat(vec, eps=1e-6):
    """
    Converts scaled axis-angle to quat.
    Args:
        vec (tensor): (..., 3) tensor where final dim is (ax,ay,az) axis-angle exponential coordinates
        eps (float): Stability value below which small values will be mapped to 0

    Returns:
        tensor: (..., 4) tensor where final dim is (x,y,z,w) vec4 float quaternion
    """
    # type: (Tensor, float) -> Tensor
    # store input shape and reshape
    input_shape = vec.shape[:-1]
    vec = vec.reshape(-1, 3)

    # Grab angle
    angle = th.norm(vec, dim=-1, keepdim=True)

    # Create return array
    quat = th.zeros(th.prod(th.tensor(input_shape)), 4, device=vec.device)
    quat[:, 3] = 1.0

    # Grab indexes where angle is not zero an convert the input to its quaternion form
    idx = angle.reshape(-1) > eps  # th.nonzero(angle).reshape(-1)
    quat[idx, :] = th.cat(
        [vec[idx, :] * th.sin(angle[idx, :] / 2.0) / angle[idx, :], th.cos(angle[idx, :] / 2.0)], dim=-1
    )

    # Reshape and return output
    quat = quat.reshape(
        list(input_shape)
        + [
            4,
        ]
    )
    return quat


@th.jit.script
def pose_in_A_to_pose_in_B(pose_A, pose_A_in_B):
    """
    Converts a homogenous matrix corresponding to a point C in frame A
    to a homogenous matrix corresponding to the same point C in frame B.

    Args:
        pose_A (th.tensor): 4x4 matrix corresponding to the pose of C in frame A
        pose_A_in_B (th.tensor): 4x4 matrix corresponding to the pose of A in frame B

    Returns:
        th.tensor: 4x4 matrix corresponding to the pose of C in frame B
    """

    # pose of A in B takes a point in A and transforms it to a point in C.

    # pose of C in B = pose of A in B * pose of C in A
    # take a point in C, transform it to A, then to B
    # T_B^C = T_A^C * T_B^A
    return pose_A_in_B.dot(pose_A)


@th.jit.script
def pose_inv(pose_mat):
    """
    Computes the inverse of a homogeneous matrix corresponding to the pose of some
    frame B in frame A. The inverse is the pose of frame A in frame B.

    Args:
        pose_mat (th.tensor): 4x4 matrix for the pose to inverse

    Returns:
        th.tensor: 4x4 matrix for the inverse pose
    """

    # Note, the inverse of a pose matrix is the following
    # [R t; 0 1]^-1 = [R.T -R.T*t; 0 1]

    # Intuitively, this makes sense.
    # The original pose matrix translates by t, then rotates by R.
    # We just invert the rotation by applying R-1 = R.T, and also translate back.
    # Since we apply translation first before rotation, we need to translate by
    # -t in the original frame, which is -R-1*t in the new frame, and then rotate back by
    # R-1 to align the axis again.

    pose_inv = th.zeros((4, 4))
    pose_inv[:3, :3] = pose_mat[:3, :3].T
    pose_inv[:3, 3] = -pose_inv[:3, :3] @ pose_mat[:3, 3]
    pose_inv[3, 3] = 1.0
    return pose_inv


@th.jit.script
def pose_transform(pos1, quat1, pos0, quat0):
    """
    Conducts forward transform from pose (pos0, quat0) to pose (pos1, quat1):

    pose1 @ pose0, NOT pose0 @ pose1

    Args:
        pos1: (x,y,z) position to transform
        quat1: (x,y,z,w) orientation to transform
        pos0: (x,y,z) initial position
        quat0: (x,y,z,w) initial orientation

    Returns:
        2-tuple:
            - (th.tensor) (x,y,z) position array in cartesian coordinates
            - (th.tensor) (x,y,z,w) orientation array in quaternion form
    """
    # Get poses
    mat0 = pose2mat((pos0, quat0))
    mat1 = pose2mat((pos1, quat1))

    # Multiply and convert back to pos, quat
    return mat2pose(mat1 @ mat0)


@th.jit.script
def invert_pose_transform(pos, quat):
    """
    Inverts a pose transform

    Args:
        pos: (x,y,z) position to transform
        quat: (x,y,z,w) orientation to transform

    Returns:
        2-tuple:
            - (th.tensor) (x,y,z) position array in cartesian coordinates
            - (th.tensor) (x,y,z,w) orientation array in quaternion form
    """
    # Get pose
    mat = pose2mat((pos, quat))

    # Invert pose and convert back to pos, quat
    return mat2pose(pose_inv(mat))


@th.jit.script
def relative_pose_transform(pos1, quat1, pos0, quat0):
    """
    Computes relative forward transform from pose (pos0, quat0) to pose (pos1, quat1), i.e.: solves:

    pose1 = pose0 @ transform

    Args:
        pos1: (x,y,z) position to transform
        quat1: (x,y,z,w) orientation to transform
        pos0: (x,y,z) initial position
        quat0: (x,y,z,w) initial orientation

    Returns:
        2-tuple:
            - (th.tensor) (x,y,z) position array in cartesian coordinates
            - (th.tensor) (x,y,z,w) orientation array in quaternion form
    """
    # Get poses
    mat0 = pose2mat((pos0, quat0))
    mat1 = pose2mat((pos1, quat1))

    # Invert pose0 and calculate transform
    return mat2pose(pose_inv(mat0) @ mat1)


@th.jit.script
def _skew_symmetric_translation(pos_A_in_B):
    """
    Helper function to get a skew symmetric translation matrix for converting quantities
    between frames.

    Args:
        pos_A_in_B (th.tensor): (x,y,z) position of A in frame B

    Returns:
        th.tensor: 3x3 skew symmetric translation matrix
    """
    return th.tensor(
        [
            [0.0, -pos_A_in_B[2].item(), pos_A_in_B[1].item()],
            [pos_A_in_B[2].item(), 0.0, -pos_A_in_B[0].item()],
            [-pos_A_in_B[1].item(), pos_A_in_B[0].item(), 0.0],
        ],
        dtype=th.float32,
        device=pos_A_in_B.device,
    )


@th.jit.script
def vel_in_A_to_vel_in_B(vel_A, ang_vel_A, pose_A_in_B):
    """
    Converts linear and angular velocity of a point in frame A to the equivalent in frame B.

    Args:
        vel_A (th.tensor): (vx,vy,vz) linear velocity in A
        ang_vel_A (th.tensor): (wx,wy,wz) angular velocity in A
        pose_A_in_B (th.tensor): 4x4 matrix corresponding to the pose of A in frame B

    Returns:
        2-tuple:

            - (th.tensor) (vx,vy,vz) linear velocities in frame B
            - (th.tensor) (wx,wy,wz) angular velocities in frame B
    """
    pos_A_in_B = pose_A_in_B[:3, 3]
    rot_A_in_B = pose_A_in_B[:3, :3]
    skew_symm = _skew_symmetric_translation(pos_A_in_B)
    vel_B = rot_A_in_B.dot(vel_A) + skew_symm.dot(rot_A_in_B.dot(ang_vel_A))
    ang_vel_B = rot_A_in_B.dot(ang_vel_A)
    return vel_B, ang_vel_B


@th.jit.script
def force_in_A_to_force_in_B(force_A, torque_A, pose_A_in_B):
    """
    Converts linear and rotational force at a point in frame A to the equivalent in frame B.

    Args:
        force_A (th.tensor): (fx,fy,fz) linear force in A
        torque_A (th.tensor): (tx,ty,tz) rotational force (moment) in A
        pose_A_in_B (th.tensor): 4x4 matrix corresponding to the pose of A in frame B

    Returns:
        2-tuple:

            - (th.tensor) (fx,fy,fz) linear forces in frame B
            - (th.tensor) (tx,ty,tz) moments in frame B
    """
    pos_A_in_B = pose_A_in_B[:3, 3]
    rot_A_in_B = pose_A_in_B[:3, :3]
    skew_symm = _skew_symmetric_translation(pos_A_in_B)
    force_B = rot_A_in_B.T.dot(force_A)
    torque_B = -rot_A_in_B.T.dot(skew_symm.dot(force_A)) + rot_A_in_B.T.dot(torque_A)
    return force_B, torque_B


@th.jit.script
def rotation_matrix(angle: float, direction: th.Tensor, point: Optional[th.Tensor] = None) -> th.Tensor:
    """
    Returns matrix to rotate about axis defined by point and direction.

    E.g.:
        >>> angle = (random.random() - 0.5) * (2*math.pi)
        >>> direc = numpy.random.random(3) - 0.5
        >>> point = numpy.random.random(3) - 0.5
        >>> R0 = rotation_matrix(angle, direc, point)
        >>> R1 = rotation_matrix(angle-2*math.pi, direc, point)
        >>> is_same_transform(R0, R1)
        True

        >>> R0 = rotation_matrix(angle, direc, point)
        >>> R1 = rotation_matrix(-angle, -direc, point)
        >>> is_same_transform(R0, R1)
        True

        >>> I = numpy.identity(4, numpy.float32)
        >>> numpy.allclose(I, rotation_matrix(math.pi*2, direc))
        True

        >>> numpy.allclose(2., numpy.trace(rotation_matrix(math.pi/2,
        ...                                                direc, point)))
        True

    Args:
        angle (float): Magnitude of rotation
        direction (th.tensor): (ax,ay,az) axis about which to rotate
        point (None or th.tensor): If specified, is the (x,y,z) point about which the rotation will occur

    Returns:
        th.tensor: 4x4 homogeneous matrix that includes the desired rotation
    """
    sina = th.sin(th.tensor(angle, dtype=th.float32))
    cosa = th.cos(th.tensor(angle, dtype=th.float32))

    direction = direction / th.norm(direction)  # Normalize direction vector

    # Create rotation matrix
    R = th.eye(3, dtype=th.float32, device=direction.device)
    R *= cosa
    R += th.outer(direction, direction) * (1.0 - cosa)
    direction *= sina

    # Create the skew-symmetric matrix
    skew_matrix = th.zeros(3, 3, dtype=th.float32, device=direction.device)
    skew_matrix[0, 1] = -direction[2]
    skew_matrix[0, 2] = direction[1]
    skew_matrix[1, 0] = direction[2]
    skew_matrix[1, 2] = -direction[0]
    skew_matrix[2, 0] = -direction[1]
    skew_matrix[2, 1] = direction[0]

    R += skew_matrix

    M = th.eye(4, dtype=th.float32, device=direction.device)
    M[:3, :3] = R

    if point is not None:
        # Rotation not about origin
        point = point.to(dtype=th.float32)
        M[:3, 3] = point - th.matmul(R, point)

    return M


@th.jit.script
def clip_translation(dpos, limit):
    """
    Limits a translation (delta position) to a specified limit

    Scales down the norm of the dpos to 'limit' if norm(dpos) > limit, else returns immediately

    Args:
        dpos (n-array): n-dim Translation being clipped (e,g.: (x, y, z)) -- numpy array
        limit (float): Value to limit translation by -- magnitude (scalar, in same units as input)

    Returns:
        2-tuple:

            - (th.tensor) Clipped translation (same dimension as inputs)
            - (bool) whether the value was clipped or not
    """
    input_norm = th.norm(dpos)
    return (dpos * limit / input_norm, True) if input_norm > limit else (dpos, False)


@th.jit.script
def clip_rotation(quat, limit):
    """
    Limits a (delta) rotation to a specified limit

    Converts rotation to axis-angle, clips, then re-converts back into quaternion

    Args:
        quat (th.tensor): (x,y,z,w) rotation being clipped
        limit (float): Value to limit rotation by -- magnitude (scalar, in radians)

    Returns:
        2-tuple:

            - (th.tensor) Clipped rotation quaternion (x, y, z, w)
            - (bool) whether the value was clipped or not
    """
    clipped = False

    # First, normalize the quaternion
    quat = quat / th.norm(quat)

    den = math.sqrt(max(1 - quat[3] * quat[3], 0))
    if den == 0:
        # This is a zero degree rotation, immediately return
        return quat, clipped
    else:
        # This is all other cases
        x = quat[0] / den
        y = quat[1] / den
        z = quat[2] / den
        a = 2 * math.acos(quat[3])

    # Clip rotation if necessary and return clipped quat
    if abs(a) > limit:
        a = limit * th.sign(a) / 2
        sa = math.sin(a)
        ca = math.cos(a)
        quat = th.tensor([x * sa, y * sa, z * sa, ca])
        clipped = True

    return quat, clipped


@th.jit.script
def make_pose(translation, rotation):
    """
    Makes a homogeneous pose matrix from a translation vector and a rotation matrix.

    Args:
        translation (th.tensor): (x,y,z) translation value
        rotation (th.tensor): a 3x3 matrix representing rotation

    Returns:
        pose (th.tensor): a 4x4 homogeneous matrix
    """
    pose = th.zeros((4, 4))
    pose[:3, :3] = rotation
    pose[:3, 3] = translation
    pose[3, 3] = 1.0
    return pose


@th.jit.script
def get_orientation_error(desired, current):
    """
    This function calculates a 3-dimensional orientation error vector, where inputs are quaternions

    Args:
        desired (tensor): (..., 4) where final dim is (x,y,z,w) quaternion
        current (tensor): (..., 4) where final dim is (x,y,z,w) quaternion
    Returns:
        tensor: (..., 3) where final dim is (ax, ay, az) axis-angle representing orientation error
    """
    # convert input shapes
    input_shape = desired.shape[:-1]
    desired = desired.reshape(-1, 4)
    current = current.reshape(-1, 4)

    cc = quat_conjugate(current)
    q_r = quat_mul(desired, cc)
    return (q_r[:, 0:3] * th.sign(q_r[:, 3]).unsqueeze(-1)).reshape(list(input_shape) + [3])


@th.jit.script
def get_orientation_diff_in_radian(orn0, orn1):
    """
    Returns the difference between two quaternion orientations in radian

    Args:
        orn0 (th.tensor): (x, y, z, w)
        orn1 (th.tensor): (x, y, z, w)

    Returns:
        orn_diff (float): orientation difference in radian
    """
    vec0 = quat2axisangle(orn0)
    vec0 /= th.norm(vec0)
    vec1 = quat2axisangle(orn1)
    vec1 /= th.norm(vec1)
    return th.arccos(th.dot(vec0, vec1))


@th.jit.script
def get_pose_error(target_pose, current_pose):
    """
    Computes the error corresponding to target pose - current pose as a 6-dim vector.
    The first 3 components correspond to translational error while the last 3 components
    correspond to the rotational error.

    Args:
        target_pose (th.tensor): a 4x4 homogenous matrix for the target pose
        current_pose (th.tensor): a 4x4 homogenous matrix for the current pose

    Returns:
        th.tensor: 6-dim pose error.
    """
    error = th.zeros(6)

    # compute translational error
    target_pos = target_pose[:3, 3]
    current_pos = current_pose[:3, 3]
    pos_err = target_pos - current_pos

    # compute rotational error
    r1 = current_pose[:3, 0]
    r2 = current_pose[:3, 1]
    r3 = current_pose[:3, 2]
    r1d = target_pose[:3, 0]
    r2d = target_pose[:3, 1]
    r3d = target_pose[:3, 2]
    rot_err = 0.5 * (th.linalg.cross(r1, r1d) + th.linalg.cross(r2, r2d) + th.linalg.cross(r3, r3d))

    error[:3] = pos_err
    error[3:] = rot_err
    return error


@th.jit.script
def matrix_inverse(matrix):
    """
    Helper function to have an efficient matrix inversion function.

    Args:
        matrix (th.tensor): 2d-array representing a matrix

    Returns:
        th.tensor: 2d-array representing the matrix inverse
    """
    return th.linalg.inv_ex(matrix).inverse


@th.jit.script
def vecs2axisangle(vec0, vec1):
    """
    Converts the angle from unnormalized 3D vectors @vec0 to @vec1 into an axis-angle representation of the angle

    Args:
        vec0 (th.tensor): (..., 3) (x,y,z) 3D vector, possibly unnormalized
        vec1 (th.tensor): (..., 3) (x,y,z) 3D vector, possibly unnormalized
    """
    # Normalize vectors
    vec0 = normalize(vec0, dim=-1)
    vec1 = normalize(vec1, dim=-1)

    # Get cross product for direction of angle, and multiply by arcos of the dot product which is the angle
    return th.linalg.cross(vec0, vec1) * th.arccos((vec0 * vec1).sum(-1, keepdim=True))


@th.jit.script
def vecs2quat(vec0: th.Tensor, vec1: th.Tensor, normalized: bool = False) -> th.Tensor:
    """
    Converts the angle from unnormalized 3D vectors @vec0 to @vec1 into a quaternion representation of the angle

    Args:
        vec0 (th.Tensor): (..., 3) (x,y,z) 3D vector, possibly unnormalized
        vec1 (th.Tensor): (..., 3) (x,y,z) 3D vector, possibly unnormalized
        normalized (bool): If True, @vec0 and @vec1 are assumed to already be normalized and we will skip the
            normalization step (more efficient)

    Returns:
        th.Tensor: (..., 4) Normalized quaternion representing the rotation from vec0 to vec1
    """
    # Normalize vectors if requested
    if not normalized:
        vec0 = normalize(vec0, dim=-1)
        vec1 = normalize(vec1, dim=-1)

    # Half-way Quaternion Solution -- see https://stackoverflow.com/a/11741520
    cos_theta = th.sum(vec0 * vec1, dim=-1, keepdim=True)
    quat_unnormalized = th.where(
        cos_theta == -1,
        th.tensor([1.0, 0.0, 0.0, 0.0], device=vec0.device, dtype=vec0.dtype).expand_as(vec0),
        th.cat([th.linalg.cross(vec0, vec1), 1 + cos_theta], dim=-1),
    )
    return quat_unnormalized / th.norm(quat_unnormalized, dim=-1, keepdim=True)


@th.jit.script
def l2_distance(v1, v2):
    """Returns the L2 distance between vector v1 and v2."""
    return th.norm(th.tensor(v1) - th.tensor(v2))


@th.jit.script
def frustum(left: float, right: float, bottom: float, top: float, znear: float, zfar: float) -> th.Tensor:
    """Create view frustum matrix."""
    assert right != left, "right must not equal left"
    assert bottom != top, "bottom must not equal top"
    assert znear != zfar, "znear must not equal zfar"

    M = th.zeros((4, 4), dtype=th.float32)
    M[0, 0] = 2.0 * znear / (right - left)
    M[2, 0] = (right + left) / (right - left)
    M[1, 1] = 2.0 * znear / (top - bottom)
    M[2, 1] = (top + bottom) / (top - bottom)
    M[2, 2] = -(zfar + znear) / (zfar - znear)
    M[3, 2] = -2.0 * znear * zfar / (zfar - znear)
    M[2, 3] = -1.0
    return M


@th.jit.script
def ortho(left, right, bottom, top, znear, zfar):
    """Create orthonormal projection matrix."""
    assert right != left
    assert bottom != top
    assert znear != zfar

    M = th.zeros((4, 4), dtype=th.float32)
    M[0, 0] = 2.0 / (right - left)
    M[1, 1] = 2.0 / (top - bottom)
    M[2, 2] = -2.0 / (zfar - znear)
    M[3, 0] = -(right + left) / (right - left)
    M[3, 1] = -(top + bottom) / (top - bottom)
    M[3, 2] = -(zfar + znear) / (zfar - znear)
    M[3, 3] = 1.0
    return M


@th.jit.script
def perspective(fovy, aspect, znear, zfar):
    """Create perspective projection matrix."""
    # fovy is in degree
    assert znear != zfar
    h = th.tan(fovy / 360.0 * math.pi) * znear
    w = h * aspect
    return frustum(-w, w, -h, h, znear, zfar)


@th.jit.script
def cartesian_to_polar(x, y):
    """Convert cartesian coordinate to polar coordinate"""
    rho = th.sqrt(x**2 + y**2)
    phi = th.arctan2(y, x)
    return rho, phi


def deg2rad(deg):
    return deg * math.pi / 180.0


def rad2deg(rad):
    return rad * 180.0 / math.pi


@th.jit.script
def check_quat_right_angle(quat: th.Tensor, atol: float = 5e-2) -> th.Tensor:
    """
    Check by making sure the quaternion is some permutation of +/- (1, 0, 0, 0),
    +/- (0.707, 0.707, 0, 0), or +/- (0.5, 0.5, 0.5, 0.5)
    Because orientations are all normalized (same L2-norm), every orientation should have a unique L1-norm
    So we check the L1-norm of the absolute value of the orientation as a proxy for verifying these values

    Args:
        quat (th.Tensor): (x,y,z,w) quaternion orientation to check
        atol (float): Absolute tolerance permitted

    Returns:
        th.Tensor: Boolean tensor indicating whether the quaternion is a right angle or not
    """
    l1_norm = th.abs(quat).sum(dim=-1)
    reference_norms = th.tensor([1.0, 1.414, 2.0], device=quat.device, dtype=quat.dtype)
    return th.any(th.abs(l1_norm.unsqueeze(-1) - reference_norms) < atol, dim=-1)


@th.jit.script
def z_angle_from_quat(quat):
    """Get the angle around the Z axis produced by the quaternion."""
    rotated_X_axis = quat_apply(quat, th.tensor([1, 0, 0], dtype=th.float32))
    return th.arctan2(rotated_X_axis[1], rotated_X_axis[0])


@th.jit.script
def z_rotation_from_quat(quat):
    """
    Get the quaternion for the rotation around the Z axis produced by the quaternion.

    Args:
        quat (th.tensor): (x,y,z,w) float quaternion

    Returns:
        th.tensor: (x,y,z,w) float quaternion representing rotation around Z axis
    """
    # Ensure quat is 2D tensor
    if quat.dim() == 1:
        quat = quat.unsqueeze(0)

    # Get the yaw angle from the quaternion
    _, _, yaw = quat2euler(quat)

    # Create a new quaternion representing rotation around Z axis
    z_quat = th.zeros_like(quat)
    z_quat[:, 2] = th.sin(yaw / 2)  # z component
    z_quat[:, 3] = th.cos(yaw / 2)  # w component

    # If input was 1D, return 1D
    if quat.shape[0] == 1:
        z_quat = z_quat.squeeze(0)

    return z_quat


@th.jit.script
def integer_spiral_coordinates(n):
    """A function to map integers to 2D coordinates in a spiral pattern around the origin."""
    # Map integers from Z to Z^2 in a spiral pattern around the origin.
    # Sources:
    # https://www.reddit.com/r/askmath/comments/18vqorf/find_the_nth_coordinate_of_a_square_spiral/
    # https://oeis.org/A174344
    m = th.floor(th.sqrt(n))
    x = ((-1) ** m) * ((n - m * (m + 1)) * (th.floor(2 * th.sqrt(n)) % 2) - math.ceil(m / 2))
    y = ((-1) ** (m + 1)) * ((n - m * (m + 1)) * (th.floor(2 * th.sqrt(n) + 1) % 2) + math.ceil(m / 2))
    return int(x), int(y)


@th.jit.script
def calculate_xy_plane_angle(quaternion: th.Tensor) -> th.Tensor:
    """
    Compute the 2D orientation angle from a quaternion assuming the initial forward vector is along the x-axis.

    Parameters:
    quaternion : th.Tensor
        The quaternion (w, x, y, z) representing the rotation.

    Returns:
    th.Tensor
        The angle (in radians) of the projection of the forward vector onto the XY plane.
        Returns 0.0 if the projected vector's magnitude is negligibly small.
    """
    fwd = quat_apply(quaternion, th.tensor([1.0, 0.0, 0.0], dtype=quaternion.dtype, device=quaternion.device))
    fwd_xy = fwd.clone()
    fwd_xy[..., 2] = 0.0

    norm = th.norm(fwd_xy, dim=-1, keepdim=True)

    # Use where to handle both cases
    angle = th.where(norm < 1e-4, th.zeros_like(norm), th.arctan2(fwd_xy[..., 1], fwd_xy[..., 0]))

    return angle.squeeze(-1)
