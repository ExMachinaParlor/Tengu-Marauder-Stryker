#!/bin/bash

# Clone the repository
git clone https://github.com/ExMachinaParlor/fusion-hat.git

# Navigate into the directory
cd fusion-hat || { echo "Failed to enter directory"; exit 1; }

# Run the install script
sudo python3 install.py
if [ $? -ne 0 ]; then
    echo "Installation failed. Please check the output for errors."
    exit 1
fi
