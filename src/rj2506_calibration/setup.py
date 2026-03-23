from setuptools import setup
import os
from glob import glob

package_name = 'rj2506_calibration'

setup(
    name=package_name,
    version='0.0.1',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='rj2506',
    maintainer_email='rj2506@todo.todo',
    description='Calibration tools for RJ2506 project',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'eye_in_hand_collector = scripts.eye_in_hand_collector:main',
            'tcp_calibrator = scripts.tcp_calibrator:main',
        ],
    },
)
