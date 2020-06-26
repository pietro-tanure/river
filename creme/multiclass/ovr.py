import collections
import copy

import pandas as pd

from creme import base


__all__ = ['OneVsRestClassifier']


class OneVsRestClassifier(base.Wrapper, base.MultiClassifier):
    """One-vs-the-rest (OvR) multiclass strategy.

    This strategy consists in fitting one binary classifier per class. Because we are in a
    streaming context, the number of classes isn't known from the start, hence new classifiers are
    instantiated on the fly. Likewise, the predicted probabilities will only include the classes
    seen up to a given point in time.

    Parameters:
        classifier: A binary classifier, although a multi-class classifier will work too.

    Attributes:
        classifiers (dict): A mapping between classes and classifiers.

    Example:

        >>> from creme import datasets
        >>> from creme import linear_model
        >>> from creme import metrics
        >>> from creme import model_selection
        >>> from creme import multiclass
        >>> from creme import preprocessing

        >>> dataset = datasets.ImageSegments()

        >>> scaler = preprocessing.StandardScaler()
        >>> ovr = multiclass.OneVsRestClassifier(linear_model.LogisticRegression())
        >>> model = scaler | ovr

        >>> metric = metrics.MacroF1()

        >>> model_selection.progressive_val_score(dataset, model, metric)
        MacroF1: 0.774573

        This estimator also also supports mini-batching.

        >>> for X in pd.read_csv(dataset.path, chunksize=64):
        ...     y = X.pop('category')
        ...     y_pred = model.predict_many(X)
        ...     model = model.fit_many(X, y)

    """

    def __init__(self, classifier: base.BinaryClassifier):
        self.classifier = classifier
        self.classifiers = {}
        self._y_name = None

    @property
    def _wrapped_model(self):
        return self.classifier

    def fit_one(self, x, y):

        # Instantiate a new binary classifier if the class is new
        if y not in self.classifiers:
            self.classifiers[y] = copy.deepcopy(self.classifier)

        # Train each label's associated classifier
        for label, model in self.classifiers.items():
            model.fit_one(x, y == label)

        return self

    def predict_proba_one(self, x):

        y_pred = {}
        total = 0.

        for label, model in self.classifiers.items():
            yp = model.predict_proba_one(x)[True]
            y_pred[label] = yp
            total += yp

        if total:
            for label in y_pred:
                y_pred[label] /= total

        return y_pred

    def fit_many(self, X, y, **params):

        self._y_name = y.name

        # Instantiate a new binary classifier for the classes that have not yet been seen
        for label in y.unique():
            if label not in self.classifiers:
                self.classifiers[label] = copy.deepcopy(self.classifier)

        # Train each label's associated classifier
        for label, model in self.classifiers.items():
            model.fit_many(X, y == label, **params)

        return self

    def predict_proba_many(self, X):

        y_pred = pd.DataFrame(columns=self.classifiers.keys(), index=X.index)

        for label, clf in self.classifiers.items():
            y_pred[label] = clf.predict_proba_many(X)[True]

        return y_pred.div(y_pred.sum(axis='columns'), axis='rows')

    def predict_many(self, X):
        if not self.classifiers:
            return pd.Series([None] * len(X), index=X.index, dtype='object')
        return self.predict_proba_many(X).idxmax(axis='columns').rename(self._y_name)
