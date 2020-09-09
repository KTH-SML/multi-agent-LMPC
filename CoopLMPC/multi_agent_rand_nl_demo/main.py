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

from LTV_FTOCP import LTV_FTOCP
from init_FTOCP import init_FTOCP
from NL_FTOCP import NL_FTOCP
from NL_LMPC import NL_LMPC
from agents import DT_Kin_Bike_Agent

from utils.plot_bike_utils import plot_bike_agent_trajs
from utils.lmpc_visualizer import lmpc_visualizer
from utils.safe_set_utils import get_safe_set, get_safe_set_2, inspect_safe_set
import utils.utils

def solve_init_traj(ftocp, x_0, model_agent, agt_idx, agt_trajs, visualizer=None, tol=-7, timeout=100):
	n_x = ftocp.n_x
	n_u = ftocp.n_u

	xcl_feas = [x_0]
	ucl_feas = []
	t_span = []

	model_dt = model_agent.dt
	control_dt = ftocp.agent.dt
	n_control = int(np.around(control_dt/model_dt))

	waypts = ftocp.get_x_refs()
	waypt_idx = ftocp.get_reference_idx()

	ftocp.build_opti_solver(agt_idx)
	ftocp.build_opti0_solver(agt_idx)

	t = 0
	counter = 0
	success = False

	x_guess = np.zeros((n_x, ftocp.N+1))
	u_guess = np.zeros((n_u, ftocp.N))
	# u_t = np.zeros(n_u)

	if visualizer is not None:
		th = np.linspace(0, 2*np.pi, 100)
		for i in range(len(agt_trajs)):
			visualizer.pos_ax.plot(agt_trajs[i][0,:], agt_trajs[i][1,:], 'k.')
			visualizer.pos_ax.plot(agt_trajs[i][0,0]+model_agent.r*np.cos(th), agt_trajs[i][1,0]+model_agent.r*np.sin(th), 'b')
			visualizer.pos_ax.plot(agt_trajs[i][0,-1]+model_agent.r*np.cos(th), agt_trajs[i][1,-1]+model_agent.r*np.sin(th), 'r')
		visualizer.fig.canvas.draw()

	# time Loop (Perform the task until close to the origin)
	while True:
		if counter >= timeout:
			print('Timeout reached, stopping...')
			break

		if np.mod(counter, n_control) == 0:
			if t == 0:
				x_pred, u_pred, feas = ftocp.solve_opti0(counter, x_0, agt_trajs, x_guess=x_guess, u_guess=u_guess, verbose=False)
			else:
				x_t = xcl_feas[-1] # Read measurements
				x_pred, u_pred, feas = ftocp.solve_opti(counter, x_t, u_t, agt_trajs, x_guess=x_guess, u_guess=u_guess, verbose=False)

			if not feas:
				xcl_feas = None
				ucl_feas = None
				success = False
				# pdb.set_trace()
				return xcl_feas, ucl_feas, success

			if t == 0:
				xcl_feas = [x_pred[:,0]]
				x_t = x_pred[:,0]

			u_t = u_pred[:,0]
			print('t: %g, d: %g, x: %g, y: %g, phi: %g, v: %g' % (t, la.norm(x_t[:2] - waypts[waypt_idx][:2]), x_t[0], x_t[1], x_t[2]*180.0/np.pi, x_t[3]))

		# Read input and apply it to the system
		x_tp1 = model_agent.sim(x_t, u_t)

		ucl_feas.append(u_t)
		xcl_feas.append(x_tp1)

		x_guess = x_pred
		u_guess = u_pred

		if visualizer is not None:
			visualizer.plot_traj(np.array(xcl_feas).T, np.array(ucl_feas).T, x_pred, u_pred, counter, shade=False)
		# Close within tolerance
		d = la.norm(x_tp1[:2] - waypts[waypt_idx][:2])
		v = x_tp1[3] - waypts[waypt_idx][3]
		if d <= 0.5 and waypt_idx < len(waypts)-1:
			print('Waypoint %i reached' % waypt_idx)
			ftocp.advance_reference_idx()
			waypt_idx = ftocp.get_reference_idx()
		elif d <= 1.5 and np.abs(v) <= 10**tol and waypt_idx == len(waypts)-1:
			print('Goal state reached')
			success = True
			break

		t += model_dt
		counter += 1

	if success:
		xcl_feas = np.array(xcl_feas).T
		ucl_feas = np.array(ucl_feas).T
	else:
		xcl_feas = None
		ucl_feas = None


	return xcl_feas, ucl_feas, success

def solve_lmpc(lmpc, x_0, x_f, model_agent, verbose=False, visualizer=None, pause=False, tol=-7):
	n_x = lmpc.n_x
	n_u = lmpc.n_u

	x_ol = []
	u_ol = []

	xcl = x_0.reshape((-1,1)) # initialize system state
	ucl = np.empty((n_u,0))

	inspect = False
	solve_times = []

	t = 0
	# time Loop (Perform the task until close to the origin)
	while True:
		x_t = xcl[:,t] # Read measurements
		solve_start = time.time()
		x_pred, u_pred, cost, SS, N = lmpc.solve(t, x_t, x_f, tol, verbose=verbose) # Solve FTOCP
		solve_end = time.time()
		solve_time = solve_end - solve_start
		# Inspect incomplete trajectory
		if x_pred is None or u_pred is None and visualizer is not None:
			# visualizer.traj_inspector(xcl, ucl, x_ol, u_ol, t, lmpc.SS_t, lmpc.expl_constrs)
			sys.exit()
		else:
			x_ol.append(x_pred)
			u_ol.append(u_pred)

		u_t = u_pred[:,0]
		# Read input and apply it to the system
		x_tp1 = model_agent.sim(x_t, u_t)

		ucl = np.append(ucl, u_t.reshape((-1,1)), axis=1)
		xcl = np.append(xcl, x_tp1.reshape((-1,1)), axis=1)

		solve_times.append(solve_time)
		print('ts: %g, d: %g, x: %g, y: %g, phi: %g, v: %g' % (t, la.norm(x_tp1-x_f, ord=2), x_t[0], x_t[1], x_t[2]*180.0/np.pi, x_t[3]))

		if visualizer is not None:
			visualizer.plot_traj(xcl, ucl, x_pred, u_pred, t, SS, lmpc.expl_constrs, shade=False)

		if la.norm(x_tp1-x_f, ord=2) <= 10**tol:
			print('Tolerance reached, agent reached goal state')
			break

		if pause:
			pdb.set_trace()

		t += 1

	# Inspection mode after iteration completion
	# if inspect:
		# visualizer.traj_inspector(xcl, ucl, x_pred_log, u_pred_log, t, lmpc.SS_t, lmpc.expl_constrs)

	return xcl, ucl, x_ol, u_ol, solve_times

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--init_traj', action='store_true', help='Use trajectory from file', default=False)
	parser.add_argument('--from_checkpoint', type=str, help='Directory of checkpoint to start from', default=None)
	args = parser.parse_args()

	out_dir = '/'.join((BASE_DIR, 'out'))
	if not os.path.exists(out_dir):
		os.makedirs(out_dir)

	log_dir = '/'.join((BASE_DIR, 'logs'))
	if not os.path.exists(log_dir):
		os.makedirs(log_dir)

	if args.from_checkpoint is not None:
		checkpoint_dir = '/'.join((out_dir, args.from_checkpoint))

	# Flags
	plot_init = False # Plot initial trajectory
	pause_each_solve = False # Pause on each FTOCP solution

	plot_lims = [[-10, 10], [-10, 10]]
	# plot_lims = [[-6, 6], [-6, 6]]
	# tol = -4
	tol = -3

	model_dt = 0.1
	control_dt = 0.1

	n_a = 10 # Number of agents
	n_x = 4 # State dimension
	n_u = 2 # Input dimension

	# Car dimensions
	l_r = 0.5
	l_f = 0.5
	w = 0.5

	col_buf = [0.2 for _ in range(n_a)]

	# Initialize dynamics and control agents (allows for dynamics to be simulated with higher resolution than control rate)
	model_agents = [DT_Kin_Bike_Agent(l_r, l_f, w, model_dt, col_buf=col_buf[i], v_lim=[-2.0, 2.0]) for i in range(n_a)]
	lmpc_control_agents = [DT_Kin_Bike_Agent(l_r, l_f, w, control_dt, col_buf=col_buf[i], v_lim=[-10.0, 10.0]) for i in range(n_a)]

	if args.from_checkpoint is None:
		if not args.init_traj:
			# ====================================================================================
			# Run NL MPC to compute feasible solutions for all agents
			# ====================================================================================

			# Random initial conditions
			# x_0 = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]
			# Goal conditions (these will be updated once the initial trajectories are found)
			# x_f = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]

			x_0 = [np.array([1,3,-np.pi/4,0]),
				np.array([-2,1,-np.pi/2,0]),
				np.array([5,8,-3*np.pi/4,0]),
				np.array([-4,-9,np.pi,0]),
				np.array([-8,-8,-np.pi/4,0]),
				np.array([8,3,-3*np.pi/4,0]),
				np.array([2,-7,0,0]),
				np.array([-7,0,-3*np.pi/4,0]),
				np.array([-8,8,np.pi/4,0]),
				np.array([-7,4,3*np.pi/4,0])]

			x_f = [np.array([-9,9,np.pi/2,0]),
				np.array([8,-8,-np.pi/4,0]),
				np.array([-5,3,-3*np.pi/4,0]),
				np.array([7,-2,np.pi,0]),
				np.array([-4,8,np.pi/2,0]),
				np.array([-3,-3,-3*np.pi/4,0]),
				np.array([9,9,np.pi,0]),
				np.array([0,-9,-np.pi/2,0]),
				np.array([3,7,0,0]),
				np.array([4,-1,-np.pi/4,0])]

			# Initialize lists to store feasible trajectories for agents
			xcl_feas = []
			ucl_feas = []

			# Initialize FTOCP objects
			# Initial trajectory MPC parameters for each agent
			Q = np.diag([20.0, 20.0, 1.0, 25.0])
			# R = np.diag([10.0, 30.0])
			R = np.diag([10.0, 10.0])
			# Rd = np.diag([10.0, 30.0])
			Rd = np.diag([10.0, 10.0])

			# R = np.diag([100.0, 50.0])
			# Rd = np.diag([100.0, 50.0])
			P = Q
			N = 60

			if Q.shape[0] != Q.shape[1] or len(np.diag(Q)) != n_x:
				raise(ValueError('Q matrix not shaped properly'))
			if R.shape[0] != R.shape[1] or len(np.diag(R)) != n_u:
				raise(ValueError('Q matrix not shaped properly'))

			start = time.time()
			# for i in range(n_a):
			agt_idx = 0
			while agt_idx < n_a:
				# while True:
				# 	coll_free = True
				# 	rand_conf_0 = np.multiply(np.random.uniform(size=(3,)),(np.array([9, 9, 2*np.pi])-np.array([-9, -9, 0]))) + np.array([-9, -9, 0])
				# 	rand_conf_f = np.multiply(np.random.uniform(size=(3,)),(np.array([9, 9, 2*np.pi])-np.array([-9, -9, 0]))) + np.array([-9, -9, 0])
				# 	if la.norm(rand_conf_f[:2]-rand_conf_0[:2]) < 10.0:
				# 		continue
				# 	for i in range(agt_idx):
				# 		d = model_agents[i].get_collision_buff_r()+model_agents[agt_idx].get_collision_buff_r()
				# 		if la.norm(rand_conf_f[:2]-x_0[i][:2]) < 1.5*d or la.norm(rand_conf_f[:2]-x_f[i][:2]) < 1.5*d \
				# 			or la.norm(rand_conf_0[:2]-x_0[i][:2]) < 1.5*d or la.norm(rand_conf_0[:2]-x_f[i][:2]) < 1.5*d:
				# 			coll_free = False
				# 			break
				#
				# 	if coll_free:
				# 		x_f[agt_idx] = np.append(rand_conf_f, 0)
				# 		x_0[agt_idx] = np.append(rand_conf_0, 0)
				# 		break

				mpc_agent = DT_Kin_Bike_Agent(l_r, l_f, w, control_dt, col_buf=col_buf[agt_idx], v_lim=[-2.0, 2.0], a_lim=[-1.0, 1.0], df_lim=[-0.35, 0.35], da_lim=[-3.0, 3.0], ddf_lim=[-0.5, 0.5])
				# ocp = LTV_FTOCP(Q, P, R, Rd, N, mpc_agent, x_refs=[x_f[agt_idx]])
				ocp = init_FTOCP(Q, P, R, Rd, N, mpc_agent, x_refs=[x_f[agt_idx]])
				vis = lmpc_visualizer(pos_dims=[0,1], n_state_dims=n_x, n_act_dims=n_u, agent_id=agt_idx, n_agents=n_a, plot_lims=plot_lims)

				print('Solving initial trajectory for agent %i' % (agt_idx+1))
				x, u, success = solve_init_traj(ocp, x_0[agt_idx], model_agents[agt_idx], agt_idx, xcl_feas[:agt_idx], visualizer=vis, tol=tol, timeout=300)
				if success:
					xcl_feas.append(x)
					ucl_feas.append(u)

					x_0[agt_idx] = x[:,0]
					x_f[agt_idx] = x[:,-1]

					agt_idx += 1
					N = 60
				else:
					N -= 10
					if N == 0:
						pdb.set_trace()

				vis.close_figure()

			end = time.time()

			for i in range(n_a):
				x_last = xcl_feas[i][:,-1]
				x_last[3] = 0.0
				# xcl_feas[i] = np.append(xcl_feas[i], x_last.reshape((n_x,1)), axis=1)
				ucl_feas[i] = np.append(ucl_feas[i], np.zeros((n_u,1)), axis=1)

				# Save initial trajecotry if file doesn't exist
				if not os.path.exists('/'.join((FILE_DIR, 'init_traj_%i.npz' % i))):
					print('Saving initial trajectory for agent %i' % (i+1))
					np.savez('/'.join((FILE_DIR, 'init_traj_%i.npz' % i)), x=xcl_feas[i], u=ucl_feas[i])

			print('Time elapsed: %g s' % (end - start))
		else:
			# Load initial trajectory from file

			x_f = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]
			x_0 = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]
			xcl_feas = []
			ucl_feas = []
			for i in range(n_a):
				init_traj = np.load('/'.join((FILE_DIR, 'init_traj_%i.npz' % i)), allow_pickle=True)
				xcl_feas.append(init_traj['x'])
				ucl_feas.append(init_traj['u'])
				x_0[i] = xcl_feas[i][:,0]

		# Shift agent trajectories in time so that they occur sequentially
		# (no collisions)
		xcl_lens = [xcl_feas[i].shape[1] for i in range(n_a)]

		if plot_init:
			plot_bike_agent_trajs(xcl_feas, ucl_feas, model_agents, model_dt, trail=True, plot_lims=plot_lims, it=0)

		# Set goal state to be last state of initial trajectories
		for i in range(n_a):
			x_f[i] = xcl_feas[i][:,-1]

	pdb.set_trace()

	# ====================================================================================

	# ====================================================================================
	# Run LMPC
	# ====================================================================================

	if args.from_checkpoint is not None:
		lmpc = pickle.load(open(checkpoint_dir + '/lmpc.pkl', 'rb'))
		xcls = pickle.load(open(checkpoint_dir + '/x_cls.pkl', 'rb'))
		ucls = pickle.load(open(checkpoint_dir + '/u_cls.pkl', 'rb'))

		# Set goal state to be last state of initial trajectories
		x_f = [xcls[0][i][:,-1] for i in range(n_a)]
	else:
		# Initialize LMPC objects for each agent
		N_LMPC = [30 for _ in range(n_a)] # horizon lengths
		# N_LMPC = [15, 15, 15] # horizon lengths
		lmpc_ftocp = [NL_FTOCP(lmpc_control_agents[i]) for i in range(n_a)]# ftocp solve by LMPC
		lmpc = [NL_LMPC(f, N_LMPC[i]) for f in lmpc_ftocp]# Initialize the LMPC

		xcls = [copy.copy(xcl_feas)]
		ucls = [copy.copy(ucl_feas)]

	ss_n_t = 80
	ss_n_j = 1

	totalIterations = 10 # Number of iterations to perform
	start_time = time.strftime("%Y-%m-%d_%H-%M-%S")
	exp_dir = '/'.join((out_dir, start_time))
	os.makedirs(exp_dir)

	# Initialize visualizer for each agent
	lmpc_vis = [lmpc_visualizer(pos_dims=[0,1], n_state_dims=n_x, n_act_dims=n_u, agent_id=i, n_agents=n_a, plot_lims=plot_lims) for i in range(n_a)]
	# lmpc_vis = None

	it_times = []
	agent_times = []
	agent_solve_times = []
	print('Starting multi-agent LMPC...')

	# run simulation
	# iteration loop
	for it in range(totalIterations):
		print('======================== Iteration %i ========================' % (it+1))
		it_dir = '/'.join((exp_dir, 'it_%i' % (it+1)))
		os.makedirs(it_dir)

		plot_bike_agent_trajs(xcls[-1], ucls[-1], model_agents, model_dt, trail=True, plot_lims=plot_lims, save_dir=exp_dir, save_video=True, it=it)

		# pdb.set_trace()
		it_start = time.time()

		# Compute safe sets and exploration spaces along previous trajectory
		# ss_idxs, expl_constrs = get_safe_set(xcls, lmpc_control_agents, ss_n_t, ss_n_j)
		ss_idxs, expl_constrs = get_safe_set_2(xcls, lmpc_control_agents, ss_n_t, ss_n_j)

		# inspect_safe_set(xcls, ucls, ss_idxs, expl_constrs, plot_lims)

		for i in range(n_a):
			print('Adding trajectories and updating safe sets for agent %i' % (i+1))
			lmpc[i].addTrajectory(xcls[-1][i], ucls[-1][i], x_f[i]) # Add feasible trajectory to the safe set
			lmpc[i].update_safe_sets(ss_idxs[i])
			lmpc[i].update_exploration_constraints(expl_constrs[i])

		if lmpc_vis is not None:
			for lv in lmpc_vis:
				lv.update_prev_trajs(state_traj=xcls, act_traj=ucls)

		x_cl_it = []
		u_cl_it = []
		x_ol_it = []
		u_ol_it = []

		# agent loop
		agent_time = []
		agent_solve_time = []
		for i in range(n_a):
			print('=================================================')
			print('Solving trajectory for agent %i' % (i+1))
			agent_start = time.time()
			agent_dir = '/'.join((it_dir, 'agent_%i' % (i+1)))
			os.makedirs(agent_dir)
			if lmpc_vis[i] is not None:
				lmpc_vis[i].set_save_dir(agent_dir)

			x_cl, u_cl, x_ol, u_ol, solve_t = solve_lmpc(lmpc[i], x_0[i], x_f[i], model_agents[i], visualizer=lmpc_vis[i], pause=pause_each_solve, tol=tol)
			u_cl= np.append(u_cl, np.zeros((n_u,1)), axis=1)

			x_cl_it.append(x_cl)
			u_cl_it.append(u_cl)
			x_ol_it.append(x_ol)
			u_ol_it.append(u_ol)

			agent_end = time.time()
			agent_time.append(agent_end - agent_start)
			agent_solve_time.append(solve_t)
			print('Time elapsed: %g, trajectory length: %i' % (agent_end-agent_start, x_cl.shape[1]))

		xcls.append(x_cl_it)
		ucls.append(u_cl_it)

		it_end = time.time()
		it_times.append(it_end - it_start)
		agent_times.append(agent_time)
		agent_solve_times.append(agent_solve_time)
		print('Time elapsed for iteration %i: %g s' % (it+1, it_end - it_start))

		# Save iteration data
		pickle.dump(lmpc, open('/'.join((it_dir, 'lmpc.pkl')), 'wb'))
		pickle.dump(ss_idxs, open('/'.join((it_dir, 'ss.pkl')), 'wb'))
		pickle.dump(expl_constrs, open('/'.join((it_dir, 'exp_constr.pkl')), 'wb'))
		pickle.dump(xcls, open('/'.join((it_dir, 'x_cls.pkl')), 'wb'))
		pickle.dump(ucls, open('/'.join((it_dir, 'u_cls.pkl')), 'wb'))
		pickle.dump(x_ol_it, open('/'.join((it_dir, 'x_ol.pkl')), 'wb'))
		pickle.dump(u_ol_it, open('/'.join((it_dir, 'u_ol.pkl')), 'wb'))
		pickle.dump(it_times, open('/'.join((it_dir, 'it_times.pkl')), 'wb'))
		pickle.dump(agent_times, open('/'.join((it_dir, 'agent_times.pkl')), 'wb'))
		pickle.dump(agent_solve_times, open('/'.join((it_dir, 'agent_solve_times.pkl')), 'wb'))

	# Plot last trajectory
	plot_bike_agent_trajs(xcls[-1], ucls[-1], model_agents, model_dt, trail=True, plot_lims=plot_lims, save_dir=exp_dir, save_video=True, it=it)
	#=====================================================================================

	plt.show()

if __name__== "__main__":
  main()
