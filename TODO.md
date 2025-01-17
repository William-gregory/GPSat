## TODO:

- [ ] identify source of small memory lead in inline_example.py 
- [ ] update DataLoader docstrings
- [X] allow use of parquet files instead of hdf5 for storing observation data (raw/binned)
- [ ] determine if a reduced set of requirements can be used for building docs
- [ ] determine full requirements for using tensorflow >= 2.16.1
- [X] setup github action to create notebooks from example scripts (py), change link in README.md
- [ ] provide more detailed overview on how GPSat works in documentation 
- [X] add example configs to command line examples
- [ ] review logs for document generation and resolve sources of error
- [ ] add a method that checks for tables in hdf5 file
- [ ] Check can update Tensorflow / GPFlow to latest version without causing breaks
- [ ] Update setup.py to handle package installs - specifically handle different environments
- [ ] Update this README.md file, point to examples
- [ ] used argparse to read in configuration files / parameters to scripts instead of sys.argv
- [ ] Examples: sea ice, ocean elevation, simulated data
- [ ] Complete unit testing (pytests).
- [X] Specify which gpytorch version should be used.
- [X] add setuptools to requirements (provides distutils from python 3.12) 
- [X] determine why docstrings for DataLoader (and others) are missing from sphinx documents
- [X] Allowable output types. How to save and load hyperparameters/variational parameters (individual?). Best database?
- [X] update unit tests to remove sources of warnings
