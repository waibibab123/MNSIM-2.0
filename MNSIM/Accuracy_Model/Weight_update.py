import sys
import os
import math
import random
import configparser as cp
import numpy as np
work_path = os.path.dirname(os.path.dirname(os.getcwd()))
sys.path.append(work_path)
from MNSIM.Hardware_Model import *
from MNSIM.Hardware_Model.Crossbar import crossbar
from MNSIM.Interface.interface import *

def weight_update(SimConfig_path, weight, is_SAF=0, is_Variation=0, is_Rratio=0):
    # print("Hardware config file is loaded:", SimConfig_path)
    wu_config = cp.ConfigParser()
    wu_config.read(SimConfig_path, encoding='UTF-8')
    
    SAF_dist = list(map(float, wu_config.get('Device level', 'Device_SAF').split(',')))
    variation = float(wu_config.get('Device level', 'Device_Variation'))
    device_level = int(wu_config.get('Device level', 'Device_Level'))
    assert device_level >= 0, "NVM resistance level < 0"
    device_resistance = np.array(list(map(float, wu_config.get('Device level', 'Device_Resistance').split(','))))
    assert device_level == len(device_resistance), "NVM resistance setting error"
    # assume the resistance distribution of MLC is linear
    max_value = 2 ** math.floor(math.log2(device_level)) - 1
    interval = 0
    for i in range(len(device_resistance) - 1):
        interval += 1 / device_resistance[i + 1] - 1 / device_resistance[i]
    interval /= len(device_resistance) - 1
    unit_conduntance = max_value/(1/device_resistance[-1])
    total_difference = 0
    total_size = 0
    for i in range(len(weight)):
        if weight[i] is not None:
            for label, value in weight[i].items():
                # print(value.shape)
                if (is_Rratio|is_Variation):
                    for j in range(len(device_resistance)):
                        temp_resistance = 0
                        if(is_Variation):
                            temp_resistance = np.random.normal(loc=0,scale=device_resistance[j] * variation / 100)
                        value = np.where(value == j, 1/(device_resistance[j]+temp_resistance)*unit_conduntance, value)
                if (is_SAF):
                    SAF = np.random.random_sample(value.shape)
                    value_bkp = value
                    # noise_saf0 = np.where(SAF < float(SAF_dist[0] / 100), 1, 0)
                    # print("noise_saf0", noise_saf0.sum()/noise_saf0.size)
                    # noise_saf1 = np.where(SAF > 1 - float(SAF_dist[-1] / 100), 1, 0)
                    # print("noise_saf1", noise_saf1.sum()/noise_saf1.size)
                    value = np.where(SAF < float(SAF_dist[0] / 100), 0, value)
                    value = np.where(SAF > 1 - float(SAF_dist[-1] / 100), max_value, value)
                    # value = np.where(SAF > 1 - float(SAF_dist[-1] / 100), 1, value)
                    difference = np.where(value_bkp==value, 0, 1)
                    total_difference += difference.sum()
                    total_size += value.size
                weight[i].update({label: value.astype(float)})
    # print(total_size, total_difference/total_size)
    #    print true error rate
    return weight

if __name__ == '__main__':
    SimConfig_path = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), "SimConfig.ini")
    weights_file_path = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())),"cifar10_vgg8_params.pth")
    __TestInterface = TrainTestInterface('vgg8', 'MNSIM.Interface.cifar10', SimConfig_path,
                                         weights_file_path, 0)
    structure_file = __TestInterface.get_structure()
    weight = __TestInterface.get_net_bits()
    weight_2 = weight_update(SimConfig_path, weight, is_Variation=0,is_SAF=1,is_Rratio=0)
    print(type(weight), "\n", np.array(weight).shape, type(weight[0]), len(weight[0]), weight[0].keys())
    # print(type(weight_2), "\n", np.array(weight_2).shape, weight[0])  
    # weight = __TestInterface.get_net_bits()
    # print(__TestInterface.set_net_bits_evaluate(weight_2))









