# MT3 packages

Build Debian package:

```bash
VERSION=0.1.0 ./packaging/build-deb.sh
```

Build RPM package on Fedora/RHEL:

```bash
VERSION=0.1.0 ./packaging/build-rpm.sh
```

The packages contain the GUI and MT3 source. They intentionally do not bundle
CUDA, Python wheels, or model checkpoints. Run `gui/install.sh` after install
to create the private environment and download the selected checkpoints.
