from __future__ import division

import numpy as np
from numpy import linalg as la
import pdb
import copy

class LMPC(object):
	"""Learning Model Predictive Controller (LMPC)
	Inputs:
		- ftocp: Finite Time Optimal Control Prolem object used to compute the predicted trajectory
	Methods:
		- addTrajectory: adds a trajectory to the safe set SS and update value function
		- computeCost: computes the cost associated with a feasible trajectory
		- solve: uses ftocp and the stored data to comptute the predicted trajectory"""
	def __init__(self, ftocp, CVX):
		# Initialization
		self.ftocp = ftocp
		self.SS    = []
		self.uSS   = []
		self.Qfun  = []
		self.Q = ftocp.Q
		self.R = ftocp.R
		self.it    = 0
		self.CVX = CVX

	def addTrajectory(self, x, u, xf=None):
		if xf is None:
			xf = np.zeros((self.ftocp.n))

		# Add the feasible trajectory x and the associated input sequence u to the safe set
		self.SS.append(copy.copy(x))
		self.uSS.append(copy.copy(u))

		# Compute and store the cost associated with the feasible trajectory
		cost = self.computeCost(x, u, xf)
		self.Qfun.append(cost)
		self.ftocp.costFTOCP = cost[0] + 0.1

		# Augment iteration counter and print the cost of the trajectories stored in the safe set
		self.it = self.it + 1
		print ('Trajectory of length %i added to the Safe Set. Current Iteration: %i' % (x.shape[1], self.it))
		print "Performance of stored trajectories: \n", [self.Qfun[i][0] for i in range(self.it)]

		return cost

	def computeCost(self, x, u, xf):
		l = x.shape[1]
		# Compute the cost in a DP like strategy: start from the last point x[len(x)-1] and move backwards
		for t in range(l-1,-1,-1):
			if t == l-1: # Terminal cost
				# cost = [la.norm((self.Q**0.5).dot(x[:,t]-xf),ord=2)**2]
				# cost = [10*x[1,t]**2]
				cost = [0]
			else:
				cost.append(x[1,t]**2 + u[:,t].T.dot(self.R).dot(u[:,t]) + 1 + cost[-1])
		# Finally flip the cost to have correct order
		return np.flip(cost).tolist()

	def solve(self, xt, xf=None, abs_t=None, expl_con=None, verbose=True):
		# Solve the FTOCP. Here set terminal constraint = ConvHull(self.SS) and terminal cost = BarycentricInterpolation(self.Qfun)
		return self.ftocp.solve(xt, xf=xf, abs_t=abs_t, expl_con=expl_con,
			SS=self.SS, Qfun=self.Qfun, CVX=self.CVX, verbose=verbose)

	def get_safe_set_q_func(self):
		return (self.SS, self.uSS, self.Qfun)
