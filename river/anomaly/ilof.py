from __future__ import annotations

import functools

import pandas as pd

from river import anomaly, utils, base
from river.neighbors import SWINN
from river.neighbors.base import BaseNN, FunctionWrapper, DistanceFunc
from river.utils import VectorDict

class ILOF(anomaly.base.AnomalyDetector):
    """Incremental Local Outlier Factor (ILOF).
    ILOF Algorithm as described in the reference paper
    ----------
    The Incremental Local Outlier Factor (ILOF) is an online version of the Local Outlier Factor (LOF) used to identify outliers based on density of local neighbors.
    We consider:
        - NewPoints: new points;
        - kNN(p): the neighboors of p (the k closest points to p)
        - RkNN(p): the rev-neighboors of p (points that have p as one of their neighboors)
        - Set_upd_lrd: Set of points that need to update the local reachability distance
        - Set_upd_lof: Set of points that need to update the local outlier factor
    The algorithm here implemented based on the original one in the paper is:
        1) Insert NewPoints and calculate its distance to existing points
        2) Update the neighboors and reverse-neighboors of all the points
        3) Define sets of affected points that required update
        4) Calculate the reachability-distance from new point to neighboors (NewPoints -> kNN(NewPoints)) and from rev-neighboors to new point (RkNN(NewPoints) -> NewPoints)
        5) Update the reachability-distance for affected points: RkNN(RkNN(NewPoints)) -> RkNN(NewPoints)
        6) Update local reachability distance of affected points: lrd(Set_upd_lrd)
        7) Update local outlier factor: lof(Set_upd_lof)
        
    Parameters
    ----------
    n_neighbors : int
        The number of nearest neighbors to use for density estimation.
    window_size : int
        The size of the batch of data to be taken in at once for the model to learn
    distance_func : function that takes in dictionaries
        A distance function to use. By default, the Euclidean distance is used.
    verbose: boolean
        Whether or not to print messages
    Attributes
    ----------
    X
        A list of stored observations.
    X_batch
        A buffer to hold incoming observations until it's time to update the model.
    X_score
        A buffer to hold incoming observations until it's time to score them.
    dist_dict
        A dictionary to hold distances between observations.
    neighborhoods
        A dictionary to hold neighborhoods for each observation.
    rev_neighborhoods
        A dictionary to hold reverse neighborhoods for each observation.
    k_dist
        A dictionary to hold k-distances for each observation.
    reach_dist
        A dictionary to hold reachability distances for each observation.
    lof
        A dictionary to hold Local Outlier Factors for each observation.
    local_reach
        A dictionary to hold local reachability distances for each observation.
    skip_first
        A boolean value indicating whether to skip the first window of data.
    Example
    ----------
    from river import datasets
    import pandas as pd
    import ilof as ilof
    dataset = pd.DataFrame(datasets.CreditCard())
    #Define model
    k = 20 #k-neighboors
    ilof_river = ilof.ILOF(k, verbose=False)
    ilof_river.learn_many(dataset[0:30])
    for i in dataset[0][40:90]:
        ilof_river.learn_one(i)
    lof_score = []
    for x in dataset[0][100:120]:
        lof_score.append(ilof_river.score_one(x))
    References
    ----------
    Pokrajac, David & Lazarevic, Aleksandar & Latecki, Longin Jan. (2007). Incremental Local Outlier Detection for Data Streams. Proceedings of the 2007 IEEE Symposium on Computational Intelligence and Data Mining, CIDM 2007. 504-515. 10.1109/CIDM.2007.368917.
    """

    def __init__(
        self,
        n_neighbors: int = 10,
        verbose = True,
        distance_func: DistanceFunc = None,
        engine = None,
        maxlen = 1000000,
        warm_up = 400):
        
        if distance_func is None:
            distance_func = functools.partial(utils.math.minkowski_distance, p=2)
        self.distance_func = distance_func
        if engine is None:
            engine = SWINN(dist_func=self.distance_func, maxlen=maxlen)
        if not isinstance(engine.dist_func, FunctionWrapper):
            engine.dist_func = FunctionWrapper(engine.dist_func)
        self.warm_up = warm_up
        self.engine = engine
        self.n_neighbors = n_neighbors
        self.X_batch: list = []
        self.X_score: list = []
        self.dist_dict: dict = {}
        self.neighborhoods: dict = {}
        self.rev_neighborhoods: dict = {}
        self.k_dist: dict = {}
        self.reach_dist: dict = {}
        self.lof: dict = {}
        self.local_reach: dict = {}
        self.verbose = verbose
        self._nn: BaseNN = self.engine.clone(include_attributes=True)
        self.count = 0

    def learn_many(self, X_batch: pd.Series):
        """
        Update the model with many incoming observations
        Parameters
        ----------
        X_batch
            A Panda Series
        """
        X_batch = X_batch[0].tolist()
        self.learn(X_batch)

    def learn_one(self, x: dict):
        """
        Update the model with one incoming observation
        Parameters
        ----------
        x
            A dictionary of feature values.
        """
        self.X_batch.append(x)
        if len(self._nn) or len(self.X_batch) > 1:
            self.learn(self.X_batch)
            self.X_batch = []

    def learn(self, X_batch: list):
        # Check for equal samples
        X_batch, equal = self.check_equal(X_batch, self._nn)
        
        if equal != 0 and self.verbose:
            print("%i samples are equal to previous data" % equal)
            
        if len(X_batch) == 0:
            if self.verbose:
                print("No new data was added")
        else:
            # Increase size of objects to acomodate new data
            (   n_x,
                n_new,
                self._nn,
                self.neighborhoods,
                self.rev_neighborhoods,
                self.k_dist,
                self.reach_dist,
                self.dist_dict,
                self.local_reach,
                self.lof,
            ) = self.expand_objects(
                X_batch,
                self._nn,
                self.neighborhoods,
                self.rev_neighborhoods,
                self.k_dist,
                self.reach_dist,
                self.dist_dict,
                self.local_reach,
                self.lof)

            # Calculate neighborhoods, reverse neighborhoods, k-distances and distances between neighboors
            (   self.neighborhoods, self.rev_neighborhoods, self.k_dist, self.dist_dict) = self.initial_calculations(
                self._nn, n_x, n_new, self.neighborhoods, self.rev_neighborhoods, self.k_dist, self.dist_dict )

            # Define sets of particles
            (   Set_new_points, Set_rev_neighbors, Set_upd_lrd, Set_upd_lof,
            ) = self.define_sets(n_x, n_new, self.neighborhoods, self.rev_neighborhoods)

            # Calculate new reachability distance of all affected points
            self.reach_dist = self.calc_reach_dist_newpoints(
                Set_new_points, self.neighborhoods, self.rev_neighborhoods, self.reach_dist, self.dist_dict, self.k_dist )
            self.reach_dist = self.calc_reach_dist_otherpoints(
                Set_rev_neighbors, self.neighborhoods, self.rev_neighborhoods, self.reach_dist, self.dist_dict, self.k_dist )

            # Calculate new local reachability distance of all affected points
            self.local_reach = self.calc_local_reach_dist(
                Set_upd_lrd, self.neighborhoods, self.reach_dist, self.local_reach )

            # Calculate new Local Outlier Factor of all affected points
            self.lof = self.calc_lof(Set_upd_lof, self.neighborhoods, self.local_reach, self.lof)

    def score_one(self, x: VectorDict, window_score=1):
        """
        Score incoming observations based on model constructed previously.
        Perform same calculations as 'learn_one' function but doesn't add the new calculations to the atributes
        Data samples that are equal to samples stored by the model are not considered.
        Parameters
        ----------
        x
            A dictionary of feature values.
        window_score
            The size of the batch of data to be taken in at once for the model to score
        Returns
        -------
        lof : list
            List of LOF calculated for incoming data
        """
        self.X_score.append(x)

        if len(self.X_score) >= window_score:
            # Check for equal samples
            self.X_score, equal = self.check_equal(self.X_score, self._nn)
            
            if equal != 0 and self.verbose: print("%i samples are equal to previous data" % equal)

            if len(self.X_score) == 0:
                if self.verbose: print("No new data was added")
            else:                
                # Increase size of objects to acomodate new data
                (   n_x,
                    n_new,
                    nn,
                    neighborhoods,
                    rev_neighborhoods,
                    k_dist,
                    reach_dist,
                    dist_dict,
                    local_reach,
                    lof,
                ) = self.expand_objects(
                    self.X_score,
                    self._nn,
                    self.neighborhoods,
                    self.rev_neighborhoods,
                    self.k_dist,
                    self.reach_dist,
                    self.dist_dict,
                    self.local_reach,
                    self.lof)

                # Calculate neighborhoods, reverse neighborhoods, k-distances and distances between neighboors
                neighborhoods, rev_neighborhoods, k_dist, dist_dict = self.initial_calculations(
                    nn, n_x, n_new, neighborhoods, rev_neighborhoods, k_dist, dist_dict)
                
                # Define sets of particles
                (   Set_new_points, Set_rev_neighbors, Set_upd_lrd, Set_upd_lof
                ) = self.define_sets(n_x, n_new, neighborhoods, rev_neighborhoods)
                
                # Calculate new reachability distance of all affected points
                reach_dist = self.calc_reach_dist_newpoints(
                    Set_new_points, neighborhoods, rev_neighborhoods, reach_dist, dist_dict, k_dist)
                reach_dist = self.calc_reach_dist_otherpoints(
                    Set_rev_neighbors, neighborhoods, rev_neighborhoods, reach_dist, dist_dict, k_dist)
                
                # Calculate new local reachability distance of all affected points
                local_reach = self.calc_local_reach_dist(
                    Set_upd_lrd, neighborhoods, reach_dist, local_reach)
                
                # Calculate new Local Outlier Factor of all affected points
                lof = self.calc_lof(Set_upd_lof, neighborhoods, local_reach, lof)
                self.X_score = []

                score_keys = list(range(n_x, n_x + n_new))
                return [lof[i] for i in score_keys]

    def initial_calculations(
        self,
        nn: list,
        n_x: tuple,
        n_new: tuple,
        neighborhoods: dict,
        rev_neighborhoods: dict,
        k_distances: dict,
        dist_dict: dict):
        """
        Perform initial calculations on the incoming data before applying the ILOF algorithm.
        Taking the new data, it updates the neighborhoods, reverse neighborhoods, k-distances and distances between particles.
        Parameters
        ----------
        X
            A list of stored observations.
        nm : tuple of ints, (n, m)
            A tuple representing the current size of the dataset.
        neighborhoods : dict
            A dictionary of particle neighborhoods.
        rev_neighborhoods : dict
            A dictionary of reverse particle neighborhoods.
        k_distances : dict
            A dictionary to hold k-distances for each observation.
        dist_dict : dict of dicts
            A dictionary of dictionaries storing distances between particles
        Returns
        -------
        neighborhoods : dict
            Updated dictionary of particle neighborhoods
        rev_neighborhoods : dict
            Updated dictionary of reverse particle neighborhoods
        k_distances : dict
            Updated dictionary to hold k-distances for each observation
        dist_dict : dict of dicts
            Updated dictionary of dictionaries storing distances between particles
        """
        k = self.n_neighbors

        # Calculate distances all particles consdering new and old ones
        new_distances = self.calculate_dist(n_x, n_new, nn)
        
        # Add new distances to distance dictionary
        for i in range(len(new_distances)):
            dist_dict[new_distances[i][0]][new_distances[i][1]] = new_distances[i][2]
            dist_dict[new_distances[i][1]][new_distances[i][0]] = new_distances[i][2]

        # Calculate new k-dist for each particle
        for i, inner_dict in enumerate(dist_dict.values()):
            k_distances[i] = sorted(inner_dict.values())[min(k, len(inner_dict.values())) - 1]

        # Only keep particles that are neighbors in distance dictionary
        dist_dict = { k: {k2: v2 for k2, v2 in v.items() if v2 <= k_distances[k]}
                    for k, v in dist_dict.items() }

        # Define new neighborhoods for particles
        for key, value in dist_dict.items():
            neighborhoods[key] = [index for index in value]

        # Define new reverse neighborhoods for particles
        for particle_id, neighbor_ids in neighborhoods.items():
            for neighbor_id in neighbor_ids:
                rev_neighborhoods[neighbor_id].append(particle_id)

        return neighborhoods, rev_neighborhoods, k_distances, dist_dict

    def calculate_dist(self, n_x, n_new, X):
        #relação entre novos e existentes calcular por força bruta, entre novos e novos pelo swimm
        if len(X) < self.warm_up:
            new_distances = [[i, j, self.distance_func(X[i].item[0], X[j].item[0])] 
                for i in range(n_x + n_new) for j in range(i) if i >= n_x ]
        else: 
            new_distances=[]
            for i in range(n_x, n_x+n_new):
                k,v = X.search(X[i].item, n_neighbors=self.n_neighbors)
                new_distances += [[X[i].item[1],p[1],d] for p,d in zip(k,v) if X[i].item[1]!=p[1]]
        return new_distances

    def check_equal(self, X: list, Y: list):
        """Check if new batch X has some data samples equal to previous data recorded Y"""
        result = [x for x in X if not any(x == y.item[0] for y in Y)]
        return result, len(X) - len(result)

    def expand_objects(
        self,
        new_particles: list,
        nn,
        neighborhoods: dict,
        rev_neighborhoods: dict,
        k_dist: dict,
        reach_dist: dict,
        dist_dict: dict,
        local_reach: dict,
        lof: dict ):
        
        """Expand size of dictionaries and lists to fit new data"""
        n_x = len(nn._data)
        n_new = len(new_particles)
        for new_particle in new_particles:
            nn.append((new_particle, self.count))
            self.count+=1
        neighborhoods.update({i: [] for i in range(n_x + n_new)})
        rev_neighborhoods.update({i: [] for i in range(n_x + n_new)})
        k_dist.update({i: float("inf") for i in range(n_x + n_new)})
        reach_dist.update({i + n_x: {} for i in range(n_new)})
        dist_dict.update({i + n_x: {} for i in range(n_new)})
        local_reach.update({i + n_x: [] for i in range(n_new)})
        lof.update({i + n_x: [] for i in range(n_new)})
        return (
            n_x,
            n_new,
            nn,
            neighborhoods,
            rev_neighborhoods,
            k_dist,
            reach_dist,
            dist_dict,
            local_reach,
            lof )

    def define_sets(self, n_x, n_new, neighborhoods: dict, rev_neighborhoods: dict):
        """Define sets of points for the ILOF algorithm"""
        # Define set of new points from batch
        Set_new_points = set(range(n_x, n_x + n_new))
        Set_rev_neighbors: set = set()

        # Define reverse neighbors of new data points
        for i in Set_new_points:
            Set_rev_neighbors = set(Set_rev_neighbors) | set(rev_neighborhoods[i])

        # Define points that need to update their local reachability distance because of new data points
        Set_upd_lrd = Set_rev_neighbors
        for j in Set_rev_neighbors:
            Set_upd_lrd = Set_upd_lrd | set(rev_neighborhoods[j])
        Set_upd_lrd = Set_upd_lrd | Set_new_points

        # Define points that need to update their lof because of new data points
        Set_upd_lof = Set_upd_lrd
        for m in Set_upd_lrd:
            Set_upd_lof = Set_upd_lof | set(rev_neighborhoods[m])
        Set_upd_lof = Set_upd_lof
        return Set_new_points, Set_rev_neighbors, Set_upd_lrd, Set_upd_lof

    def calc_reach_dist_newpoints(
        self, Set: set, neighborhoods: dict, rev_neighborhoods: dict, reach_dist: dict, dist_dict: dict, k_dist: dict):
        """Calculate reachability distance from new points to neighbors and from neighbors to new points"""
        for c in Set:
            for j in set(neighborhoods[c]):
                reach_dist[c][j] = max(dist_dict[c][j], k_dist[j])
            for j in set(rev_neighborhoods[c]):
                reach_dist[j][c] = max(dist_dict[j][c], k_dist[c])
        return reach_dist

    def calc_reach_dist_otherpoints(
        self, Set: set, neighborhoods: dict, rev_neighborhoods: dict, reach_dist: dict, dist_dict: dict, k_dist: dict):
        """Calculate reachability distance from reverse neighbors of reverse neighbors ( RkNN(RkNN(NewPoints)) ) to reverse neighbors ( RkNN(NewPoints) )
        These values change because of the insertion of new points"""
        for j in Set:
            for i in set(rev_neighborhoods[j]):
                reach_dist[i][j] = max(dist_dict[i][j], k_dist[j])
        return reach_dist

    def calc_local_reach_dist(
        self, Set: set, neighborhoods: dict, reach_dist: dict, local_reach_dist: dict):
        """Calculate local reachability distance of affected points"""
        for i in Set:
            local_reach_dist[i] = len(neighborhoods[i]) / sum(
                [reach_dist[i][j] for j in neighborhoods[i]])
        return local_reach_dist

    def calc_lof(
        self, Set: set, neighborhoods: dict, local_reach: dict, lof: dict):
        """Calculate local outlier factor of affected points"""
        for i in Set:
            lof[i] = sum([local_reach[j] for j in neighborhoods[i]]) / (
                len(neighborhoods[i]) * local_reach[i])
        return lof