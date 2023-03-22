from PyOptimalInterpolation.models.base_model import BaseGPRModel

try:
    from PyOptimalInterpolation.models.gpflow_models import GPflowGPRModel, GPflowSGPRModel, GPflowSVGPModel
except:
    print("Could not load GPflow models. Check if GPflow 2 is installed")

try:
    from PyOptimalInterpolation.models.sklearn_models import sklearnGPRModel
except:
    print("Could not load sklearn model. Check if scikit-learn is installed")

try:
    from PyOptimalInterpolation.models.vff_model import GPflowVFFModel
except:
    print("Could not load VFF model. Check if GPflow 2 is installed")

