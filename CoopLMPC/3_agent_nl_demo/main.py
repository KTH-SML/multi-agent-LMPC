from __future__ import division

import numpy as np
import numpy.linalg as la
import multiprocessing as mp

import matplotlib
matplotlib.use('TkAgg')
from matplotlib import rc
rc('text', usetex=True)

import matplotlib.pyplot as plt

import os, sys, time, copy, pickle, itertools, pdb, argparse

os.environ['TZ'] = 'America/Los_Angeles'
time.tzset()
FILE_DIR =  os.path.dirname('/'.join(str.split(os.path.realpath(__file__),'/')))
BASE_DIR = os.path.dirname('/'.join(str.split(os.path.realpath(__file__),'/')[:-2]))
sys.path.append(BASE_DIR)

from FTOCP_coop import FTOCP
from LMPC_coop import LMPC
from agents import Kin_Bike_Agent

import utils.plot_utils
import utils.utils

def solve_init_traj(ftocp, x0, waypt, xf, tol=-7):
	n_x = ftocp.n
	n_u = ftocp.d

	xcl_feas = x0
	ucl_feas = np.empty((n_u,0))

	if waypt is None:
		mode = 1
	else:
		mode = 0
	t = 0

	# time Loop (Perform the task until close to the origin)
	while True:
		xt = xcl_feas[:,t] # Read measurements

		if mode == 0:
			xg = waypt.reshape((n_x))
			act_tol = 0
		else:
			xg = xf.reshape((n_x))
			act_tol = tol

		(x_pred, u_pred) = ftocp.solve(xt, t, xf=xg, CVX=True, verbose=False) # Solve FTOCP

		# Read input and apply it to the system
		ut = u_pred[:,0].reshape((n_u, 1))
		xtp1 = ftocp.model(xt.reshape((n_x, 1)), ut)
		ucl_feas = np.append(ucl_feas, ut, axis=1)
		xcl_feas = np.append(xcl_feas, xtp1, axis=1)

		# print('Time step: %i, Distance: %g' % (t, la.norm(xtp1-xf.reshape((n_x,1)), ord=2)))
		# Close within tolerance
		if la.norm(xtp1-xg.reshape((n_x,1)), ord=2) <= 10**act_tol:
			if mode == 1:
				break
			mode += 1

		t += 1

	return (xcl_feas, ucl_feas)

def solve_lmpc(lmpc, x0, xf, expl_con=None, verbose=False, visualizer=None, pause=False, tol=-7):
	n_x = lmpc.ftocp.n
	n_u = lmpc.ftocp.d

	x_pred_log = []
	u_pred_log = []

	xcl = x0 # initialize system state at interation it
	ucl = np.empty((n_u,0))

	inspect = False

	t = 0
	# time Loop (Perform the task until close to the origin)
	while True:
		xt = xcl[:,t] # Read measurements
		(x_pred, u_pred) = lmpc.solve(xt, t, xf=xf, expl_con=expl_con, verbose=verbose) # Solve FTOCP
		# Inspect incomplete trajectory
		if x_pred is None or u_pred is None:
			utils.utils.traj_inspector(visualizer, t, xcl, x_pred_log, u_pred_log, expl_con)
			sys.exit()
		else:
			x_pred_log.append(x_pred)
			u_pred_log.append(u_pred)

		if visualizer is not None:
			visualizer.plot_state_traj(xcl, x_pred, t, expl_con=expl_con, shade=True)
			visualizer.plot_act_traj(ucl, u_pred, t)

		# Read input and apply it to the system
		ut = u_pred[:,0].reshape((n_u, 1))
		xtp1 = lmpc.ftocp.model(xt.reshape((n_x, 1)), ut)

		ucl = np.append(ucl, ut, axis=1)
		xcl = np.append(xcl, xtp1, axis=1)

		# print('Time step: %i, Distance: %g' % (t, la.norm(xtp1-xf.reshape((n_x,1)), ord=2)))
		if la.norm(xtp1-xf.reshape((n_x,1)), ord=2) <= 10**tol:
			break

		if pause:
			raw_input('Iteration %i. Press enter to continue: ' % t)

		t += 1

	# Inspection mode after iteration completion
	if inspect:
		utils.utils.traj_inspector(visualizer, t, xcl, x_pred_log, u_pred_log, expl_con)

	# print np.round(np.array(xcl).T, decimals=2) # Uncomment to print trajectory
	return xcl, ucl

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--init_traj', type=str, help='File containing the inital trajectory for each agent')
	args = parser.parse_args()

	out_dir = '/'.join((BASE_DIR, 'out'))
	if not os.path.exists(out_dir):
		os.makedirs(out_dir)

	log_dir = '/'.join((BASE_DIR, 'logs'))
	if not os.path.exists(log_dir):
		os.makedirs(log_dir)

	# Flags
	parallel = False # Parallelization flag
	plot_init = False # Plot initial trajectory
	pause_each_solve = False # Pause on each FTOCP solution

	plot_lims = [[-2.5, 2.5], [-2.5, 2.5]]
	tol = -5
	# tol = -3

	dt = 0.01
	n_a = 3 # Number of agents
	n_x = 4 # State dimension
	n_u = 2 # Input dimension

	l_r = 0.2
	l_f = 0.2
	w = 0.2

	# Initial Condition
	x0 = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]
	x0[0] = np.array([[-1, 1, 0, 0]]).T
	x0[1] = np.array([[1, 1, 0, 0]]).T
	x0[2] = np.array([[2, 0, 0, 0]]).T

	# Goal condition
	xf = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]
	xf[0] = np.array([[1, -1, 0, 0]]).T
	xf[1] = np.array([[-1, -1, 0, 0]]).T
	xf[2] = np.array([[-2, 0, 0, 0]]).T

	agents = [Kin_Bike_Agent(l_r, l_f, w, dt, x_0[i], x_f[i]) for i in range(n_a)]

	r_a = [agents[i].get_collision_buff_r() for i in range(n_a)] # Agents are circles with radius r_a
	occupied_space = {'type' : 'circle', 'params' : r_a}

	# wh_a = [np.array([0.2,0.15]), np.array([0.2,0.15]), np.array([0.2,0.15])] # Agents are boxes with [width, height]
	# occupied_space = {'type' : 'box', 'params' : wh_a}

	# Define cost for each agent
	Q = np.diag([1.0, 1.0, 1.0, 1.0]) #np.eye(2)
	R = np.diag([0.1, 0.1]) #np.array([[1]])

	# Check to make sure all agent dynamics, inital, and goal states have been defined
	if np.any(np.isnan(x0)) or np.any(np.isnan(xf)):
		raise(ValueError('Initial or goal states have empty entries'))
	if Q.shape[0] != Q.shape[1] or len(np.diag(Q)) != n_x:
		raise(ValueError('Q matrix not shaped properly'))
	if R.shape[0] != R.shape[1] or len(np.diag(R)) != n_u:
		raise(ValueError('Q matrix not shaped properly'))

	if args.init_traj is None:
		# ====================================================================================
		# Run simulation to compute feasible solutions for all agents
		# ====================================================================================
		# Intermediate waypoint to ensure collision-free trajectory
		# waypt = [np.array([[2, 1.5, 0, 0]]).T, np.array([[0, -1.5, 0, 0]]).T]
		waypt = None

		xcl_feas = []
		ucl_feas = []

		# Initialize FTOCP objects
		N_feas = 10
		ftocp = [FTOCP(N_feas, A[i], B[i], 0.1*Q, R, Hx=Hx, gx=gx, Hu=Hu, gu=gu) for i in range(n_a)]

		start = time.time()
		if parallel:
			# Create threads
			pool = mp.Pool(processes=n_a)
			# Assign thread to agent trajectory
			results = [pool.apply_async(solve_init_traj, args=(ftocp[i], x0[i], None, xf[i], tol)) for i in range(n_a)]
			# Sync point
			init_trajs = [r.get() for r in results]

			(xcl_feas, ucl_feas) = zip(*init_trajs)
			xcl_feas = list(xcl_feas)
			ucl_feas = list(ucl_feas)
		else:
			for i in range(n_a):
				(x, u) = solve_init_traj(ftocp[i], x0[i], None, xf[i], tol=tol)
				xcl_feas.append(x)
				ucl_feas.append(u)
		end = time.time()

		for i in range(n_a):
			xcl_feas[i] = np.append(xcl_feas[i], xf[i], axis=1)
			ucl_feas[i] = np.append(ucl_feas[i], np.zeros((n_u,2)), axis=1)

		# Shift agent trajectories in time so that they occur sequentially
		# (no collisions)
		xcl_lens = [xcl_feas[i].shape[1] for i in range(n_a)]

		for i in range(n_a):
			before_len = 0
			after_len = 0
			for j in range(i):
				before_len += xcl_lens[j]
			# for j in range(i+1, n_a):
			# 	after_len += xcl_lens[j]
			# for j in range(i):
			# 	before_len += 5
			# for j in range(i+1, n_a):
			# 	after_len += 5

			xcl_feas[i] = np.hstack((np.tile(x0[i], before_len), xcl_feas[i], np.tile(xf[i], after_len)))
			ucl_feas[i] = np.hstack((np.zeros((n_u, before_len)), ucl_feas[i], np.zeros((n_u, after_len))))

		# pdb.set_trace()

		print('Time elapsed: %g s' % (end - start))

		if plot_init:
			plot_utils.plot_agent_trajs(xcl_feas, r=r_a, trail=True)

		# Save initial trajecotry if file doesn't exist
		if not os.path.exists('/'.join((FILE_DIR, 'init_traj.pkl'))):
			print('Saving initial trajectory')
			init_traj = {'x': xcl_feas, 'u' : ucl_feas}
			pickle.dump(init_traj, open('/'.join((FILE_DIR, 'init_traj.pkl')), 'wb'))

	else:
		# Load initial trajectory from file
		init_traj = pickle.load(open('/'.join((FILE_DIR, args.init_traj)), 'rb'))
		xcl_feas = init_traj['x']
		ucl_feas = init_traj['u']

	# pdb.set_trace()
	# ====================================================================================

	# ====================================================================================
	# Run LMPC
	# ====================================================================================

	# Initialize LMPC objects for each agent
	N_LMPC = [6, 6, 6] # horizon lengths
	ftocp_for_lmpc = [FTOCP(N_LMPC[i], A[i], B[i], Q, R, Hx=Hx, gx=gx, Hu=Hu, gu=gu) for i in range(n_a)]# ftocp solve by LMPC
	lmpc = [LMPC(f, CVX=False) for f in ftocp_for_lmpc]# Initialize the LMPC decide if you wanna use the CVX hull

	xcls = [copy.copy(xcl_feas)]
	ucls = [copy.copy(ucl_feas)]

	ss_n_t = 30
	ss_n_j = 5
	# ss_idxs, expl_spaces = utils.utils.get_safe_set(xcls, xf, ss_n_t, ss_n_j, occupied_space)
	# for i in range(n_a):
	# 	print('Agent %i' % (i+1))
	# 	lmpc[i].add_safe_set(ss_idxs[i])
	# 	lmpc[i].addTrajectory(xcl_feas[i], ucl_feas[i], xf[i]) # Add feasible trajectory to the safe set

	totalIterations = 15 # Number of iterations to perform
	start_time = time.strftime("%Y-%m-%d_%H-%M-%S")
	exp_dir = '/'.join((out_dir, start_time))
	os.makedirs(exp_dir)

	# Initialize visualizer for each agent
	# lmpc_vis = [utils.plot_utils.lmpc_visualizer(pos_dims=[0,1], n_state_dims=n_x, n_act_dims=n_u, agent_id=i, plot_lims=plot_lims) for i in range(n_a)]
	lmpc_vis = [None for i in range(n_a)]

	raw_input('Ready to run LMPC, press enter to continue...')

	# run simulation
	# iteration loop
	for it in range(totalIterations):
		print('****************** Iteration %i ******************' % (it+1))
		utils.plot_utils.plot_agent_trajs(xcls[-1], r_a=r_a, trail=True, plot_lims=plot_lims, save_dir=exp_dir, it=it)

		# Compute safe sets and exploration spaces along previous trajectory
		ss_idxs, expl_spaces = utils.utils.get_safe_set(xcls, xf, ss_n_t, ss_n_j, occupied_space)
		for i in range(n_a):
			print('Agent %i' % (i+1))
			lmpc[i].add_safe_set(ss_idxs[i])
			lmpc[i].addTrajectory(xcls[-1][i], ucls[-1][i], xf[i]) # Add feasible trajectory to the safe set

		for lv in lmpc_vis:
			if lv is not None:
				lv.update_prev_trajs(state_traj=xcls[-1], act_traj=ucls[-1])

		it_start = time.time()
		# ball_con = utils.utils.get_traj_ell_con(xcls[-1], xf, r_a=r_a, tol=tol) # Compute lin_con with last trajectory
		# lin_con = utils.utils.get_traj_lin_con(xcls[-1], xf, r_a=r_a, tol=tol)

		x_it = []
		u_it = []
		# agent loop
		for i in range(n_a):
			print('Agent %i' % (i+1))
			agent_start = time.time()
			agent_dir = '/'.join((exp_dir, 'it_%i' % (it+1), 'agent_%i' % (i+1)))
			os.makedirs(agent_dir)
			if lmpc_vis[i] is not None:
				lmpc_vis[i].set_plot_dir(agent_dir)

			# expl_con = {'lin' : lin_con[i]}
			expl_con = {'lin' : expl_spaces[i]}
			(xcl, ucl) = solve_lmpc(lmpc[i], x0[i], xf[i], expl_con=expl_con, visualizer=lmpc_vis[i], pause=pause_each_solve, tol=tol)
			# opt_cost = lmpc[i].addTrajectory(xcl, ucl)
			# obj_plot.update(np.array([it, opt_cost]).T, i)
			ucl= np.append(ucl, np.zeros((n_u,1)), axis=1)

			x_it.append(xcl)
			u_it.append(ucl)

			agent_end = time.time()
			print('Time elapsed: %g, trajectory length: %i' % (agent_end-agent_start, xcl.shape[1]))

		xcls.append(x_it)
		ucls.append(u_it)

		# ss_idxs, expl_spaces = utils.utils.get_safe_set(xcls, xf, ss_n_t, ss_n_j, occupied_space)
		# for i in range(n_a):
		# 	print('Agent %i' % (i+1))
		# 	lmpc[i].add_safe_set(ss_idxs[i])
		# 	lmpc[i].addTrajectory(x_it[i], u_it[i], xf[i]) # Add feasible trajectory to the safe set

		it_end = time.time()
		print('Time elapsed for iteration %i: %g s' % (it+1, it_end - it_start))

		pickle.dump(lmpc, open('/'.join((exp_dir, 'it_%i.pkl' % (it+1))), 'wb'))

	# Plot last trajectory
	utils.plot_utils.plot_agent_trajs(xcls[-1], r_a=r_a, trail=True, plot_lims=plot_lims, save_dir=exp_dir, it=totalIterations)
	#=====================================================================================

	plt.show()

if __name__== "__main__":
  main()
