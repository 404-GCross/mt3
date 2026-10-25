# Copyright 2026 The MT3 Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Install mt3."""

import os
import sys
import setuptools

# To enable importing version.py directly, we add its path to sys.path.
version_path = os.path.join(os.path.dirname(__file__), 'mt3')
sys.path.append(version_path)
from version import __version__  # pylint: disable=g-import-not-at-top

setuptools.setup(
    name='mt3',
    version=__version__,
    description='Multi-Task Multitrack Music Transcription',
    author='Google Inc.',
    author_email='no-reply@google.com',
    url='http://github.com/magenta/mt3',
    license='Apache 2.0',
    packages=setuptools.find_packages(),
    package_data={
        '': ['*.gin'],
    },
    scripts=[],
    install_requires=[
        'absl-py',
        'fastapi',          # WebUI（gui/webui），纯 Python，约 8MB
        'flax @ git+https://github.com/google/flax#egg=flax',
        'gin-config',
        'immutabledict',
        'librosa',
        'mido',
        'mir_eval',
        'note-seq @ git+https://github.com/magenta/note-seq#egg=note_seq',
        'numpy',
        'pretty_midi',
        'python-multipart',  # WebUI 上传（multipart 表单）
        'scikit-learn',
        'scipy',
        'seqio @ git+https://github.com/google/seqio#egg=seqio',
        't5',
        't5x @ git+https://github.com/google-research/t5x#egg=t5x',
        # t5x 的 setup.py 没声明它，但 t5x/checkpoints.py 在 import 时就用到，
        # 少了会在转谱时 ModuleNotFoundError: No module named 'tensorboard'。
        'tensorboard',
        'tensorflow',
        'tensorflow-datasets',
        'uvicorn',          # WebUI 的 ASGI 服务器（不要 uvicorn[standard]，
                            # 那些 C 扩展对离线打包没好处）
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: Apache Software License',
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
    ],
    tests_require=['pytest'],
    setup_requires=['pytest-runner'],
    keywords='music transcription machinelearning audio',
)
