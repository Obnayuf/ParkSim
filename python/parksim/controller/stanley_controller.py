"""

Path tracking simulation with Stanley steering control and PID speed control.

author: Atsushi Sakai (@Atsushi_twi)

Ref:
    - [Stanley: The robot that won the DARPA grand challenge](http://isl.ecst.csuchico.edu/DOCS/darpa2005/DARPA%202005%20Stanley.pdf)
    - [Autonomous Automobile Path Tracking](https://www.ri.cmu.edu/pub_files/2009/2/Automatic_Steering_Methods_for_Autonomous_Automobile_Path_Tracking.pdf)

"""
from typing import List
import numpy as np

from parksim.controller_types import StanleyParams
from parksim.pytypes import VehicleState
from parksim.vehicle_types import VehicleBody, VehicleConfig

def normalize_angle(angle):
    """
    Normalize an angle to [-pi, pi].

    :param angle: (float)
    :return: (float) Angle in radian in [-pi, pi]
    """
    while angle > np.pi:
        angle -= 2.0 * np.pi

    while angle < -np.pi:
        angle += 2.0 * np.pi

    return angle

class StanleyController(object):
    """
    Stanley Controller
    """
    def __init__(self, control_params: StanleyParams=StanleyParams(), vehicle_body: VehicleBody=VehicleBody(), vehicle_config: VehicleConfig=VehicleConfig()):
        """Instantiate the object."""
        super().__init__()

        self.k = control_params.dt
        self.Kp = control_params.Kp
        self.Kp_braking = control_params.Kp_braking
        self.dt = control_params.dt

        self.L = vehicle_body.wb

        self.max_steer = vehicle_config.delta_max

    def calc_target_index(self, state: VehicleState, cx: List[float], cy: List[float]):
        """
        Compute index in the trajectory list of the target.

        :param state: (VehicleState object)
        :param cx: [float]
        :param cy: [float]
        :return: (int, float)
        """
        # Calc front axle position, given state and L (distance b/w front and rear wheels)
        fx = state.x.x + self.L * np.cos(state.e.psi)
        fy = state.x.y + self.L * np.sin(state.e.psi)

        # Search nearest point index
        # returns index of point that is closest to front axle
        dx = [fx - icx for icx in cx]
        dy = [fy - icy for icy in cy]
        d = np.hypot(dx, dy)
        target_idx = np.argmin(d)

        # Project RMS error onto front axle vector
        front_axle_vec = [-np.cos(state.e.psi + np.pi / 2),
                        -np.sin(state.e.psi + np.pi / 2)] # this is equivalent to [sin(yaw), -cos(yaw)]
        # this is cross-track error
        error_front_axle = np.dot([dx[target_idx], dy[target_idx]], front_axle_vec)

        return target_idx, error_front_axle

    def pid_control(self, target, current, braking=False):
        """
        Proportional control for the speed. If braking, have target speed be 0

        :param target: (float)
        :param current: (float)
        :return: (float)
        """
        # Controls acceleration — Kp is how fast we approach target speed
        if not braking:
            return self.Kp * (target - current)
        else:
            return self.Kp_braking * (target - current)


    def stanley_control(self, state: VehicleState, cx: List[float], cy: List[float], cyaw: List[float], last_target_idx):
        """
        Stanley steering control.

        :param state: (VehicleState object)
        :param cx: ([float])
        :param cy: ([float])
        :param cyaw: ([float])
        :param last_target_idx: (int)
        :return: (float, int)
        """
        # get index of waypoint we should travel to
        current_target_idx, error_front_axle = self.calc_target_index(state, cx, cy)

        # if we're moving forward, cool, otherwise keep going to where we were going before
        if last_target_idx >= current_target_idx:
            current_target_idx = last_target_idx

        # theta_e corrects the heading error
        theta_e = normalize_angle(cyaw[current_target_idx] - state.e.psi)
        # theta_d corrects based on the cross track error (k is a gain for this)
        # Cross track error: http://www.sailtrain.co.uk/gps/functions.htm
        theta_d = np.arctan2(self.k * error_front_axle, state.v.v)
        # Steering control (sum of heading correction + getting back on path)
        delta = theta_e + theta_d

        return delta, current_target_idx

    def step(self, state: VehicleState, acceleration: float, delta: float):
        """
        Update the state of the vehicle.

        Stanley Control uses bicycle model.

        :param acceleration: (float) Acceleration
        :param delta: (float) Steering
        """
        state.u.u_a = acceleration
        state.u.u_steer = delta

        # don't turn too much
        delta = np.clip(delta, -self.max_steer, self.max_steer)

        # advance x and y
        state.x.x += state.v.v * np.cos(state.e.psi) * self.dt
        state.x.y += state.v.v * np.sin(state.e.psi) * self.dt
        # advance yaw, scaled by velocity, tangent of angle, and inverse of wheelbase
        state.e.psi += state.v.v / self.L * np.tan(delta) * self.dt
        state.e.psi = normalize_angle(state.e.psi)
        # advance velocity
        state.v.v += acceleration * self.dt
