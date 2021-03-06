import os
import time
import argparse
import numpy as np
import matplotlib.pyplot as plt

import pinocchio as pnc

from jiminy_py import core as jiminy
from jiminy_py.log import extract_state_from_simulation_log
from jiminy_py.viewer import play_trajectories
from jiminy_py.core import HeatMapFunctor, heatMapType_t, ForceSensor

from interactive_plot_util import interactive_legend


SPATIAL_COORDS = ["X", "Y", "Z"]


# ################################ User parameters #######################################
# Parse arguments.
parser = argparse.ArgumentParser(description="Compute and plot inverted pendulum solutions")
parser.add_argument('-tf', '--tf', type=float, help="Solve duration.", default=2.0)
parser.add_argument('-fHLC', '--fHLC', type=float, help="HLC frequence (LLC = 1kHz).", default=200.0)
parser.add_argument('--acceleration', help='Command directly with acceleration (default position control).', action='store_true', default=False)
parser.add_argument('--plot', help='Plot.', action='store_true', default=False)
parser.add_argument('--show', help='Show gepetto animation.', action='store_true', default=False)
parser.add_argument('--targetsFB', help='Target state instead of current in com, dcm and zmp computation.', action='store_true', default=False)
parser.add_argument('--mixedFB', help='Current COM and target VCOM states for DCM computation.', action='store_true', default=False)
parser.add_argument('--clampCmd', help='Clamp zmp command.', action='store_true', default=False)
args = parser.parse_args()

tf = args.tf
fHLC = args.fHLC
acceleration_control = args.acceleration
position_control = not acceleration_control

urdf_path = os.path.join(os.environ["HOME"], "wdc_workspace/src/jiminy/data/simple_pendulum/simple_pendulum.urdf")

# ########################### Initialize the simulation #################################

# Instantiate the model
contacts = ["Corner1", "Corner2", "Corner3", "Corner4"]
motors = ["PendulumJoint"]
model = jiminy.Model()
model.initialize(urdf_path, contacts, motors, True)
model.add_force_sensor("F1", "Corner1")
model.add_force_sensor("F2", "Corner2")
model.add_force_sensor("F3", "Corner3")
model.add_force_sensor("F4", "Corner4")
iPos = model.motors_position_idx[0]
iVel = model.motors_velocity_idx[0]
axisCom = 0

# Constants
m = 75
l = 1.0
g  = 9.81
omega = np.sqrt(g/l)

# Initial values
q0 = 0.0
dq0 = -0.0
x0 = np.zeros((model.nq + model.nv, ))
x0[:model.nq] = model.pinocchio_model_th.neutralConfiguration
x0[iPos] = q0
x0[iPos+iVel] = dq0

# Compute com dcm references
nTimes = int(tf * 1e3) + 1
deltaStabilization = 0.5e3
deltaSlope = 1.0
deltaCom = 0.041
comRef = np.zeros(nTimes)
comRef[int(deltaStabilization):(int(deltaStabilization + deltaSlope * 1e3) + 1)] = \
    np.linspace(0, deltaCom, int(deltaSlope * 1e3) + 1, endpoint=False)
comRef[(int(deltaStabilization + deltaSlope * 1e3) + 1):] = deltaCom
zmpRef = comRef
dcomRef = np.zeros(nTimes)
dcomRef[int(deltaStabilization):(int(deltaStabilization + deltaSlope * 1e3) + 1)] = deltaCom/deltaSlope
ddcomRef = np.zeros(nTimes)

if args.targetsFB:
    # Gains dcm control
    Kpdcm = 15.0
    Kddcm = 2.0
    Kidcm = 1.0
    decay = 0.1
    integral_ = 0.0
    # Gains admittance
    Acom = 15.0
elif args.mixedFB:
    # Gains dcm control
    Kpdcm = 15.0
    Kddcm = 1.0
    Kidcm = 0.0
    decay = 0.0
    integral_ = 0.0
    # Gains admittance
    Acom = 7.5
else:
    # Gains dcm control
    Kpdcm = 1.0
    Kddcm = 0.0
    Kidcm = 0.5
    decay = 0.01
    integral_ = 0.0
    # Gains admittance
    Acom = 60.0
# Gains position control
Kp = (m * l**2) * 1e3
Kd = 0.0 * Kp

# Perturbation
paux = 0.02
taux = 6.0

# Utilities
def update_frame(model, data, name):
    frame_id = model.getFrameId(name)
    pnc.updateFramePlacement(model, data, frame_id)

def get_frame_placement(model, data, name):
    frame_id = model.getFrameId(name)
    return data.oMf[frame_id]

# Logging: create global variables to make sure they never get deleted
com = pnc.centerOfMass(model.pinocchio_model_th, model.pinocchio_data_th, x0)
vcom = model.pinocchio_data_th.vcom[0]
dcm = com + vcom/omega
totalWrench = pnc.Force.Zero()
zmp = np.array([zmpRef[0], 0])
zmp_cmd = zmp.copy()
state_target = np.array([0.0, 0.0])

com_log, comTarget_log, comRef_log = com.copy(), com.copy(), com.copy()
vcom_log, vcomTarget_log, vcomRef_log = vcom.copy(), vcom.copy(), vcom.copy()
dcm_log, dcmTarget_log, dcmRef_log = dcm.copy(), dcm.copy(), dcm.copy()
totalWrench_angular_log = totalWrench.angular.copy()
totalWrench_linear_log = totalWrench.linear.copy()
zmp_log, zmpTarget_log, zmpRef_log = zmp.copy(), zmp.copy(), zmp.copy()
zmp_cmd_log = zmp.copy()
state_target_log = state_target.copy()

# Instantiate the controller
t_1 = 0.0
u_1 = 0.0
qi = np.zeros((model.nq, ))
dqi = np.zeros((model.nv, ))
ddqi = np.zeros((model.nv, ))
def updateState(model, q, v, sensor_data):
    # Get dcm from current state
    pnc.forwardKinematics(model.pinocchio_model_th, model.pinocchio_data_th, q, v)
    comOut = pnc.centerOfMass(model.pinocchio_model_th, model.pinocchio_data_th, q, v)
    vcomOut = model.pinocchio_data_th.vcom[0]
    dcmOut = comOut + vcomOut / omega
    # Create zmp from forces
    forces = np.asarray(sensor_data[ForceSensor.type])
    newWrench = pnc.Force.Zero()
    for i,name in enumerate(contacts):
        update_frame(model.pinocchio_model_th, model.pinocchio_data_th, name)
        placement = get_frame_placement(model.pinocchio_model_th, model.pinocchio_data_th, name)
        wrench = pnc.Force(np.concatenate([[0.0, 0.0, forces[2, i]], np.zeros(3)]).T)
        newWrench += placement.act(wrench)
    totalWrenchOut = newWrench
    if totalWrench.linear[2] != 0:
        zmpOut = [-totalWrenchOut.angular[1] / totalWrenchOut.linear[2],
                   totalWrenchOut.angular[0] / totalWrenchOut.linear[2]]
    else:
        zmpOut = zmp_log
    return comOut, vcomOut, dcmOut, zmpOut, totalWrenchOut

def computeCommand(t, q, v, sensor_data, u):
    global com, dcm, zmp, zmp_cmd, totalWrench, qi, dqi, ddqi, t_1, u_1, integral_

    # Get trajectory
    i = int(t * 1e3) + 1
    if t > taux :
        p = paux
    else:
        p = 0
    z = zmpRef[i] + p
    c = comRef[i] + p
    vc = dcomRef[i]
    d = c + vc/omega

    # Update state
    com, vcom, dcm, zmp, totalWrench = updateState(model, q, v, sensor_data)
    comTarget, vcomTarget, dcmTarget, zmpTarget, totalWrenchTarget = updateState(model, qi, dqi, sensor_data)

    # Update logs (only the value stored by the registered variables using [:])
    dcm_log[:] = dcm
    zmp_log[:] = zmp
    com_log[:] = com
    vcom_log[:] = vcom
    dcmRef_log[0] = d
    zmpRef_log[0] = z
    comRef_log[0] = c
    vcomRef_log[0] = vc
    dcmTarget_log[:] = dcmTarget
    zmpTarget_log[:] = zmpTarget
    comTarget_log[:] = comTarget
    vcomTarget_log[:] = vcomTarget
    totalWrench_angular_log[:] = totalWrench.angular
    totalWrench_linear_log[:] = totalWrench.linear

    # Update targets at HLC frequency
    if int(t * 1e3) % int(1e3 / fHLC) == 0:

        # Compute zmp command (DCM control)
        if args.targetsFB:
            zi = zmpTarget
            di = dcmTarget
        elif args.mixedFB:
            zi = zmp
            di = com + vcomTarget/omega
        else:
            zi = zmp
            di = dcm
        # KpKdKi dcm
        integral_ = (1 - decay) * integral_ + (t - t_1) * (d - di[axisCom])
        t_1 = t
        zmp_cmd = z - (1 + Kpdcm / omega) * (d - di[axisCom]) + (Kddcm / omega) * (z - zi[axisCom]) - Kidcm / omega * integral_
        if args.clampCmd:
            np.clip(zmp_cmd, -0.1, 0.1, zmp_cmd)

        # Compute joint acceleration from ZMP command
        # Zmp-> com admittance -> com acceleration
        ax = ddcomRef[i] + Acom * (zi[axisCom] - zmp_cmd)
        # Com acceleration -> joint acceleration
        ddqi[iVel] = (ax / (l * np.cos(q[iPos]))) + (v[iVel]**2) * np.tan(q[iPos])

        # Compute joint torque from joint acceleration (ID)
        if acceleration_control:
            u_1 = m * (l**2) * ((ax / (l * np.cos(q[iPos]))) + (v[iVel]**2) * np.tan(q[iPos]) - g * np.sin(q[iPos]) / l)

    # Send last joint torque command
    if acceleration_control:
        u[0] = u_1
    # Integrate last joint acceleration + position control
    elif position_control:
        dqi[iVel] = dqi[iVel] + ddqi[iVel] * 1e-3
        qi[iPos] = qi[iPos] + dqi[iVel] * 1e-3
        u[0] = -(Kp * (q[iPos] - qi[iPos]) + Kd * (v[iVel] - dqi[iVel]))

    # Update logs (only the value stored by the registered variables using [:])
    zmp_cmd_log[:] = zmp_cmd
    state_target_log[0], state_target_log[1] = qi[iPos], dqi[iVel]

def internalDynamics(t, q, v, sensor_data, u):
    u[:] = 0.0

controller = jiminy.ControllerFunctor(computeCommand, internalDynamics)
controller.initialize(model)
controller.register_entry(["targetPositionPendulum", "targetVelocityPendulum"], state_target_log)
controller.register_entry(["zmpCmdX"], zmp_cmd_log)
controller.register_entry(["zmp" + axis for axis in ["X", "Y"]], zmp_log)
controller.register_entry(["dcm" + axis for axis in SPATIAL_COORDS], dcm_log)
controller.register_entry(["com" + axis for axis in SPATIAL_COORDS], com_log)
controller.register_entry(["vcom" + axis for axis in SPATIAL_COORDS], vcom_log)
controller.register_entry(["zmpTarget" + axis for axis in ["X", "Y"]], zmpTarget_log)
controller.register_entry(["dcmTarget" + axis for axis in SPATIAL_COORDS], dcmTarget_log)
controller.register_entry(["comTarget" + axis for axis in SPATIAL_COORDS], comTarget_log)
controller.register_entry(["vcomTarget" + axis for axis in SPATIAL_COORDS], vcomTarget_log)
controller.register_entry(["wrenchTorque" + axis for axis in SPATIAL_COORDS], totalWrench_angular_log)
controller.register_entry(["wrenchForce" + axis for axis in SPATIAL_COORDS], totalWrench_linear_log)
controller.register_entry(["dcmReference" + axis for axis in SPATIAL_COORDS], dcmRef_log)
controller.register_entry(["comReference" + axis for axis in SPATIAL_COORDS], comRef_log)
controller.register_entry(["vcomReference" + axis for axis in SPATIAL_COORDS], vcomRef_log)
controller.register_entry(["zmpReference" + axis for axis in ["X", "Y"]], zmpRef_log)

# Instantiate the engine
engine = jiminy.Engine()
engine.initialize(model, controller)

# ######################### Configuration the simulation ################################

model_options = model.get_model_options()
sensor_options = model.get_sensors_options()
engine_options = engine.get_options()
ctrl_options = controller.get_options()

model_options["dynamics"]["enableFlexibleModel"] = False
model_options["telemetry"]["enableImuSensors"] = True
model_options["telemetry"]["enableForceSensors"] = True
engine_options["telemetry"]["enableConfiguration"] = True
engine_options["telemetry"]["enableVelocity"] = True
engine_options["telemetry"]["enableAcceleration"] = True
engine_options["telemetry"]["enableCommand"] = True
engine_options["telemetry"]["enableEnergy"] = True
engine_options["world"]["gravity"][2] = -9.81
engine_options['world']['groundProfile'] = HeatMapFunctor(0.0, heatMapType_t.CONSTANT) # Force sensor frame offset.
engine_options["stepper"]["solver"] = "runge_kutta_dopri5"  # ["runge_kutta_dopri5", "explicit_euler"]
engine_options["stepper"]["tolRel"] = 1.0e-5
engine_options["stepper"]["tolAbs"] = 1.0e-4
engine_options["stepper"]["dtMax"] = 2.0e-3  # 2.0e-4 for "explicit_euler", 3.0e-3 for "runge_kutta_dopri5"
engine_options["stepper"]["iterMax"] = 100000
engine_options["stepper"]["sensorsUpdatePeriod"] = 1.0e-3
engine_options["stepper"]["controllerUpdatePeriod"] = 1.0e-3
engine_options["stepper"]["randomSeed"] = 0
engine_options['contacts']['stiffness'] = 1.0e6
engine_options['contacts']['damping'] = 2000.0*2.0
engine_options['contacts']['dryFrictionVelEps'] = 0.01
engine_options['contacts']['frictionDry'] = 5.0
engine_options['contacts']['frictionViscous'] = 5.0
engine_options['contacts']['transitionEps'] = 0.001
sensor_options['ForceSensor'] = {}
sensor_options['ForceSensor']['F1'] = {}
sensor_options['ForceSensor']['F1']["noiseStd"] = []
sensor_options['ForceSensor']['F1']["bias"] = []
sensor_options['ForceSensor']['F1']["delay"] = 0.0
sensor_options['ForceSensor']['F1']["delayInterpolationOrder"] = 0
sensor_options['ForceSensor']['F2'] = {}
sensor_options['ForceSensor']['F2']["noiseStd"] = []
sensor_options['ForceSensor']['F2']["bias"] = []
sensor_options['ForceSensor']['F2']["delay"] = 0.0
sensor_options['ForceSensor']['F2']["delayInterpolationOrder"] = 0
sensor_options['ForceSensor']['F3'] = {}
sensor_options['ForceSensor']['F3']["noiseStd"] = []
sensor_options['ForceSensor']['F3']["bias"] = []
sensor_options['ForceSensor']['F3']["delay"] = 0.0
sensor_options['ForceSensor']['F3']["delayInterpolationOrder"] = 0
sensor_options['ForceSensor']['F4'] = {}
sensor_options['ForceSensor']['F4']["noiseStd"] = []
sensor_options['ForceSensor']['F4']["bias"] = []
sensor_options['ForceSensor']['F4']["delay"] = 0.0
sensor_options['ForceSensor']['F4']["delayInterpolationOrder"] = 0

model.set_model_options(model_options)
model.set_sensors_options(sensor_options)
engine.set_options(engine_options)
controller.set_options(ctrl_options)

# ############################## Run the simulation #####################################

start = time.time()
engine.simulate(x0, tf)
end = time.time()
print("Simulation time: %03.0fms" % ((end - start) * 1.0e3))

# ############################# Extract the results #####################################

log_data, log_constants = engine.get_log()

trajectory_data_log = extract_state_from_simulation_log(log_data, model)

# Save the log in CSV
engine.write_log("/tmp/log.data", True)

# ############################ Display the results ######################################

if args.plot:
    if args.targetsFB:
        plt.figure("ZMP X")
        plt.plot(
                 log_data['Global.Time'],
                 log_data['HighLevelController.zmpTargetX'],'b',
                 log_data['Global.Time'],
                 log_data['HighLevelController.zmpCmdX'],'g',
                 log_data['Global.Time'],
                 log_data['HighLevelController.zmpReferenceX'], 'r',
                 log_data['Global.Time'],
                 log_data['HighLevelController.comReferenceX'], 'm')
        plt.legend((
                    "ZMP X (Targets)",
                    "ZMP CMD X",
                    "ZMP Reference X",
                    "COM Reference X"))
        plt.figure("DCM X")
        plt.plot(
                 log_data['Global.Time'],
                 log_data['HighLevelController.dcmTargetX'],
                 log_data['Global.Time'],
                 log_data['HighLevelController.dcmReferenceX'])
        plt.legend(("DCM X (Targets)", "DCM Reference X"))
    else:
        plt.figure("ZMP X")
        plt.plot(
                 log_data['Global.Time'],
                 log_data['HighLevelController.zmpX'],'b',
                 log_data['Global.Time'],
                 log_data['HighLevelController.zmpCmdX'],'g',
                 log_data['Global.Time'],
                 log_data['HighLevelController.zmpReferenceX'], 'r',
                 log_data['Global.Time'],
                 log_data['HighLevelController.comReferenceX'], 'm')
        plt.legend((
                    "ZMP X",
                    "ZMP CMD X",
                    "ZMP Reference X",
                    "COM Reference X"))
        plt.figure("DCM X")
        plt.plot(
                 log_data['Global.Time'],
                 log_data['HighLevelController.dcmX'],
                 log_data['Global.Time'],
                 log_data['HighLevelController.dcmReferenceX'])
        plt.legend(("DCM X", "DCM Reference X"))
    fig = plt.figure("COM X")
    ax = plt.subplot()
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.comX'],
            label = "COM X")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.comReferenceX'],
            label = "COM Ref X")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.comTargetX'],
            label = "COM X (Targets)")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.vcomX'],
            label = "VCOM X")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.vcomReferenceX'],
            label = "VCOM Ref X")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.vcomTargetX'],
            label = "VCOM X (Targets)")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.vcomX']/omega,
            label = "VCOM/omega X")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.vcomTargetX']/omega,
            label = "VCOM/omega X (Targets)")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.dcmX'],
            label = "DCM X")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.dcmReferenceX'],
            label = "DCM Ref X")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.dcmTargetX'],
            label = "DCM X (Targets)")
    ax.plot(log_data['Global.Time'],
            log_data['HighLevelController.comTargetX'] + log_data['HighLevelController.vcomTargetX']/omega,
            label = "DCM X (Mixed)")
    leg = interactive_legend(fig)
    plt.show()

if args.show:
    # Display the simulation trajectory and the reference
    play_trajectories([trajectory_data_log], speed_ratio=0.5)
