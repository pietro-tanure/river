import collections

import numpy as np
from sklearn import utils

from .. import base


__all__ = ['KMeans']


def euclidean_distance(a, b):
    return sum((a.get(k, 0) - b.get(k, 0)) ** 2 for k in set([*a.keys(), *b.keys()]))


class KMeans(base.Clusterer):
    """Incremental k-means.

    The most common way to implement batch k-means is to use Lloyd's algorithm, which consists in
    assigning all the data points to a set of cluster centers and then moving the centers
    accordingly. This requires multiple passes over the data and thus isn't applicable in a
    streaming setting.

    In this implementation we start by finding the cluster that is closest to the current
    observation. We then move the cluster's central position towards the new observation. The
    `halflife` parameter determines by how much the movement should be.

    Example
    -------
    In the following example the cluster assignments are exactly the same as when using `sklearn`'s
    batch implementation.

        #!python
        >>> import creme.cluster
        >>> import numpy as np
        >>> X = np.array([[1, 2], [1, 4], [1, 0],
        ...               [4, 2], [4, 4], [4, 0]])
        >>> k_means = creme.cluster.KMeans(n_clusters=2, halflife=0.4, sigma=3, random_state=42)
        >>> k_means = k_means.fit(X)

        >>> k_means.predict(X)
        array([0, 0, 0, 1, 1, 1], dtype=int32)

        >>> k_means.predict([[0, 0], [4, 4]])
        array([0, 1], dtype=int32)

        >>> k_means.cluster_centers_
        array([[1.17645129, 1.93067455],
        ...    [3.4742213 , 1.84015401]])

    References
    -------
    - [Sequential k-Means Clustering](http://www.cs.princeton.edu/courses/archive/fall08/cos436/Duda/C/sk_means.htm)

    """

    def __init__(self, n_clusters=8, halflife=0.5, mu=0, sigma=1, distance=euclidean_distance,
                 random_state=None):
        self.n_clusters = n_clusters
        """Maximum number of clusters to assign."""
        self.halflife = halflife
        """Amount by which to move the cluster centers, a reasonable value if between 0 and 1."""
        self.mu = mu
        """Mean of the normal distribution used to instantiate cluster positions."""
        self.sigma = sigma
        """Standard deviation of the normal distribution used to instantiate cluster positions."""
        self.distance = distance
        """Metric used to compute distances between an observation and a cluster."""
        self.rng = utils.check_random_state(random_state)
        self.centers = {
            i: collections.defaultdict(lambda: self.rng.normal(self.mu, self.sigma))
            for i in range(n_clusters)
        }

    @property
    def cluster_centers_(self):
        """Returns the cluster centers in the same format as scikit-learn."""
        return np.array([
            list(coords.values())
            for coords in self.centers.values()
        ])

    def fit_one(self, x, y=None):
        # Find the cluster with the closest center
        closest = self.predict_one(x)

        # Move the cluster's center
        for i, xi in x.items():
            self.centers[closest][i] += self.halflife * (xi - self.centers[closest][i])

        return closest

    def predict_one(self, x):
        return min(self.centers, key=lambda c: self.distance(x, self.centers[c]))
