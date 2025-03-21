#!/usr/bin/python
# -*-coding:utf-8-*-
import torch
import sys
import os
import math
import configparser as cp

work_path = os.path.dirname(os.getcwd())
sys.path.append(work_path)
from MNSIM.Hardware_Model import *
from MNSIM.Hardware_Model.Crossbar import crossbar
from MNSIM.Hardware_Model.Tile import tile
from MNSIM.Interface.interface import *
import collections
import pandas as pd


class PE_node():
    def __init__(self, PE_id=0, ltype='conv', lnum=0):
        # PE_id: the id of PE node, ltype: layer type of this PE, lnum: layer number of this PE
        self.id = PE_id
        self.type = ltype
        self.lnum = lnum
        self.inMerge_list = []
        self.outMerge = 0

    def set_inMerge(self, Merge_id):
        if Merge_id not in self.inMerge_list:
            self.inMerge_list.append(Merge_id)
            self.inMerge_list.sort()

    def set_outMerge(self, Merge_id):
        self.outMerge = Merge_id


class Merge_node():
    def __init__(self, Merge_id=0, mtype=0, lnum=0):
        # Merge_id: the id of Merge node, mtype: merge type (0: add, 1: concat, 2: pooling)
        self.id = Merge_id
        self.type = mtype
        self.lnum = lnum
        self.inPE_list = []
        self.outPE_list = []
        self.inMerge_list = []
        self.outMerge_list = []

    def set_inPE(self, PE_id):
        if PE_id not in self.inPE_list:
            self.inPE_list.append(PE_id)
            self.inPE_list.sort()

    def set_outPE(self, PE_id):
        if PE_id not in self.outPE_list:
            self.outPE_list.append(PE_id)
            self.outPE_list.sort()

    def set_inMerge(self, Merge_id):
        if Merge_id not in self.inMerge_list:
            self.inMerge_list.append(Merge_id)
            self.inMerge_list.sort()

    def set_outMerge(self, Merge_id):
        if Merge_id not in self.outMerge_list:
            self.outMerge_list.append(Merge_id)
            self.outMerge_list.sort()

# The following matrix generations aim to conduct weights mapping on tiles

def generate_normal_matrix(row, column):
    matrix = np.zeros([row, column])
    start = 0
    for i in range(row):
        for j in range(column):
            matrix[i][j] = start
            start += 1
    return matrix

def generate_snake_matrix(row, column):
    matrix = np.zeros([row, column])
    start = 0
    for i in range(row):
        for j in range(column):
            if i % 2:
                matrix[i][column - j - 1] = start
            else:
                matrix[i][j] = start
            start += 1
    return matrix

def generate_hui_matrix(row, column):
    matrix = np.zeros([row, column])
    state = 0
    stride = 1
    step = 0
    start = 0
    dl = 0
    ru = 0
    i = 0
    j = 0
    for x in range(row * column):
        if x == 0:
            matrix[i][j] = start
        else:
            if state == 0:
                j += 1
                matrix[i][j] = start
                state = 1
            elif state == 1:
                if dl == 0:
                    i += 1
                    matrix[i][j] = start
                    step += 1
                    if step == stride:
                        dl = 1
                        step = 0
                elif dl == 1:
                    j -= 1
                    matrix[i][j] = start
                    step += 1
                    if step == stride:
                        dl = 0
                        step = 0
                        stride += 1
                        state = 2
            elif state == 2:
                i += 1
                matrix[i][j] = start
                state = 3
            elif state == 3:
                if ru == 0:
                    j += 1
                    matrix[i][j] = start
                    step += 1
                    if step == stride:
                        ru = 1
                        step = 0
                elif ru == 1:
                    i -= 1
                    matrix[i][j] = start
                    step += 1
                    if step == stride:
                        ru = 0
                        step = 0
                        stride += 1
                        state = 0
        start += 1
    return matrix

def generate_zigzag_matrix(row, column):
    matrix = np.zeros([row, column])
    state = 0
    stride = 1
    step = 0
    i = 0
    j = 0
    start = 0
    for x in range(row * column):
        if x == 0:
            matrix[i][j] = start
        else:
            if state == 0:
                if j < column - 1:
                    j += 1
                    matrix[i][j] = start
                else:
                    i += 1
                    matrix[i][j] = start
                state = 1
            elif state == 1:
                i += 1
                j -= 1
                matrix[i][j] = start
                step += 1
                if i == row - 1:
                    state = 2
                    stride -= 1
                    step = 0
                elif step == stride:
                    state = 2
                    stride += 1
                    step = 0
            elif state == 2:
                if i < row - 1:
                    i += 1
                    matrix[i][j] = start
                else:
                    j += 1
                    matrix[i][j] = start
                state = 3
            elif state == 3:
                j += 1
                i -= 1
                matrix[i][j] = start
                step += 1
                if j == column - 1:
                    state = 0
                    stride -= 1
                    step = 0
                elif step == stride:
                    state = 0
                    stride += 1
                    step = 0
        start += 1
    return matrix


class TCG():
    def __init__(self, NetStruct, SimConfig_path, multiple=None):
        # NetStruct: Interface.py的get_structure函数返回的net_array
        # NetStruct: layer structure, SimConfig_path: Hardware config path, multiple: allocate more resources for some layers (i.e., duplicate)
        TCG_config = cp.ConfigParser()
        TCG_config.read(SimConfig_path, encoding='UTF-8')
        if multiple is None:
            multiple = [1] * len(NetStruct) # 量化层数量  
        self.tile = tile(SimConfig_path) # tile
        self.net = NetStruct # net_array
        self.layer_num = len(self.net) # 量化层数量
        self.layer_tileinfo = [] # 每一个量化层的tile信息列表
        self.xbar_polarity = int(TCG_config.get('Process element level', 'Xbar_Polarity')) # 默认：2
        self.tile_connection = int(TCG_config.get('Architecture level', 'Tile_Connection')) # 默认：2
        self.tile_num = list(map(int, TCG_config.get('Architecture level', 'Tile_Num').split(','))) # 默认：(64, 64)
        if self.tile_num[0] == 0: # 默认的tile数量是8 * 8
            self.tile_num[0] = 8
            self.tile_num[1] = 8
        assert self.tile_num[0] > 0, "Tile number < 0"
        assert self.tile_num[1] > 0, "Tile number < 0"
        self.tile_total_num = self.tile_num[0] * self.tile_num[1] # tile的总数量
        self.mapping_order = -1 * np.ones(self.tile_num) # 映射到tile上的顺序
        self.mapping_result = -1 * np.ones(self.tile_num) 
        start_tileid = 0
            # the start Tile id
        self.max_inbuf_size = 0   
            # the maximum input buffer size of each PE, unit: KB   每一个PE的最大输入缓冲区内存，单位：KB
        self.max_outbuf_size = 0
            # the maximum output buffer size of each tile, unit: KB   每一个tile的最大输出缓冲区的内存，单位：KB
        self.global_buf_size = 0
            # the global buffer size for accumulator   全局缓冲区的内存
        self.global_data_size = 0
        self.global_adder_num = 0
            # the global adder number in accumulator      
        self.global_multiplier_num=0
        self.global_adder_bitwidth = 8
        self.global_multiplier_bitwidth=8
        num = []    
            # track PE number of each layer 
        total_xbar_num = 0     # xbar的总数
        for layer_id in range(self.layer_num):
            layer_dict = self.net[layer_id][0][0]  # 编号为layer_id的量化层的第0个tile上的层信息
            tmp_tileinfo = collections.OrderedDict()
            layer_type = layer_dict['type']
            if self.xbar_polarity == 1:
                weight_precision = int(layer_dict['Weightbit'])
            else:
                assert self.xbar_polarity == 2, "Crossbar polarity must be 1 or 2"
                weight_precision = int(layer_dict['Weightbit']) - 1 # 来自network.py的get_net，默认weight_bit为9，计算后为8
            tmp_tileinfo['startid'] = start_tileid
            input_size = 0
            inputchannel = 0
            outputchannel = 0
            data_inbuf = 0
            data_outbuf = 0

            if layer_type == 'conv':
                tmp_tileinfo['type'] = 'conv'
                tmp_tileinfo['mx'] = math.ceil(weight_precision / self.tile.group_num) * math.ceil(int(layer_dict['Outputchannel']) / self.tile.xbar_column)
                    # mx: PE number in x-axis  x轴方向上的所需的PE数量
                    # self.tile.group_num PE中的交叉条组数（在配置文件中配置），此处为1
                tmp_tileinfo['my'] = math.ceil(int(layer_dict['Inputchannel']) / (self.tile.xbar_row // (int(layer_dict['Kernelsize']) ** 2)))
                    # my: PE number in y-axis  y轴方向上的所需的PE数量
                tmp_tileinfo['max_group'] = min(weight_precision, self.tile.group_num)
                    # max_group: maximum used groups in one PE of this layer 此处默认为1
                tmp_tileinfo['max_row'] = min((self.tile.xbar_row // (int(layer_dict['Kernelsize']) ** 2)),
                    int(layer_dict['Inputchannel'])) * (int(layer_dict['Kernelsize']) ** 2)
                    # max_row: maximum used row in one crossbar of this layer 该层xbar使用的最大的行数
                tmp_tileinfo['max_column'] = min(int(layer_dict['Outputchannel']), self.tile.xbar_column)
                    # max_column: maximum used column in one crossbar of this layer 该层xbar使用的最大列数（单次计算）
                if 'Inputindex' not in layer_dict.keys():
                    tmp_tileinfo['Inputindex'] = [-1]
                else:
                    tmp_tileinfo['Inputindex'] = list(map(int, layer_dict['Inputindex']))
                    # Inputindex: the relative index of the input layers of this layer  该层输入层的相对索引
                if 'Outputindex' not in layer_dict.keys():
                    tmp_tileinfo['Outputindex'] = [1]
                else:
                    tmp_tileinfo['Outputindex'] = list(map(int, layer_dict['Outputindex']))
                    # Outputindex: the relative index of the output layers of this layer  该层输出层的相对索引
                if len(tmp_tileinfo['Outputindex']) == 1:
                    tmp_tileinfo['is_branchin'] = -1
                else:
                    tmp_tileinfo['is_branchin'] = 1
                    # is_branchin: if this layer is the input layer of a branch  该层是否是分支的输入层
                tmp_tileinfo['is_branchout'] = 1
                    # is_branchout: if this layer is the output layer of a branch (the next layer is element_sum)  
                for i in tmp_tileinfo['Outputindex']:
                    tmp_layer = self.net[i+layer_id][0][0]
                    if tmp_layer['type'] != 'element_sum' and tmp_layer['type'] != 'element_multiply':
                        tmp_tileinfo['is_branchout'] = -1

                input_size_list = list(map(int, layer_dict['Inputsize']))
                input_size = input_size_list[0] * input_size_list[1]
                inputchannel = int(layer_dict['Inputchannel'])
                #输入特征图按照滑动窗口的第一行的数据内存，单位：字节
                data_inbuf = input_size_list[1] * int(layer_dict['Kernelsize']) * inputchannel * int(layer_dict['Inputbit'])/8
                    # assume using the line buffer structure
                outputchannel = int(layer_dict['Outputchannel'])
                data_outbuf = outputchannel*int(layer_dict['outputbit'])/8
                # buffer_size: unit Byte
            elif layer_type == 'fc':
                tmp_tileinfo['type'] = 'fc'
                tmp_tileinfo['mx'] = math.ceil(weight_precision / self.tile.group_num) * math.ceil(int(layer_dict['Outfeature']) / self.tile.xbar_column)
                    # mx: PE number in x-axis
                tmp_tileinfo['my'] = math.ceil(int(layer_dict['Infeature']) / self.tile.xbar_row)
                    # my: PE number in y-axis
                tmp_tileinfo['max_group'] = min(weight_precision, self.tile.group_num)
                    # max_group: maximum used groups in one PE of this layer
                tmp_tileinfo['max_row'] = min(int(layer_dict['Infeature']), self.tile.xbar_row)
                    # max_row: maximum used row in one crossbar of this layer
                tmp_tileinfo['max_column'] = min(int(layer_dict['Outfeature']), self.tile.xbar_column)
                    # max_row: maximum used column in one crossbar of this layer
                if 'Inputindex' not in layer_dict.keys():
                    tmp_tileinfo['Inputindex'] = [-1]
                else:
                    tmp_tileinfo['Inputindex'] = list(map(int, layer_dict['Inputindex']))
                    # Inputindex: the relative index of the input layers of this layer
                if 'Outputindex' not in layer_dict.keys():
                    tmp_tileinfo['Outputindex'] = [1]
                else:
                    tmp_tileinfo['Outputindex'] = list(map(int, layer_dict['Outputindex']))
                    # Outputindex: the relative index of the output layers of this layer
                if len(tmp_tileinfo['Outputindex']) == 1:
                    tmp_tileinfo['is_branchin'] = -1
                else:
                    tmp_tileinfo['is_branchin'] = 1
                tmp_tileinfo['is_branchout'] = 1
                # is_branchout: if this layer is the output layer of a branch (the next layer is element_sum)
                for i in tmp_tileinfo['Outputindex']:
                    if (i+layer_id) < self.layer_num:
                        tmp_layer = self.net[i + layer_id][0][0]
                        if (tmp_layer['type'] != 'element_sum' and tmp_layer['type']!= 'element_multiply'):
                            tmp_tileinfo['is_branchout'] = -1
                # is_branchin: if this layer is the input layer of a branch
                input_size = int(layer_dict['Infeature'])
                inputchannel = 1
                data_inbuf = input_size * inputchannel * int(layer_dict['Inputbit'])/8
                data_outbuf = int(layer_dict['Outfeature']) * int(layer_dict['outputbit'])/8
                # buffer_size: unit Byte
            elif layer_type == 'pooling':
                tmp_tileinfo['type'] = 'pooling'
                tmp_tileinfo['mx'] = 1
                tmp_tileinfo['my'] = 1
                tmp_tileinfo['max_row'] = 0
                tmp_tileinfo['max_column'] = 0
                tmp_tileinfo['max_group'] = 0
                if 'Inputindex' not in layer_dict.keys():
                    tmp_tileinfo['Inputindex'] = [-1]
                else:
                    tmp_tileinfo['Inputindex'] = list(map(int, layer_dict['Inputindex']))
                # Inputindex: the relative index of the input layers of this layer
                if 'Outputindex' not in layer_dict.keys():
                    tmp_tileinfo['Outputindex'] = [1]
                else:
                    tmp_tileinfo['Outputindex'] = list(map(int, layer_dict['Outputindex']))
                # Outputindex: the relative index of the output layers of this layer
                if len(tmp_tileinfo['Outputindex']) == 1:
                    tmp_tileinfo['is_branchin'] = -1
                else:
                    tmp_tileinfo['is_branchin'] = 1
                # is_branchin: if this layer is the input layer of a branch
                tmp_tileinfo['is_branchout'] = 1
                # is_branchout: if this layer is the output layer of a branch (the next layer is element_sum)
                for i in tmp_tileinfo['Outputindex']:
                    tmp_layer = self.net[i + layer_id][0][0]
                    if tmp_layer['type'] != 'element_sum' and tmp_layer['type'] != 'element_multiply':
                        tmp_tileinfo['is_branchout'] = -1
                input_size_list = list(map(int, layer_dict['Inputsize']))
                input_size = input_size_list[0] * input_size_list[1]
                inputchannel = int(layer_dict['Inputchannel'])
                data_inbuf = 0 # assume the pooling module shares the same buffer with xbar PEs
                data_outbuf = 0
                    # assume the buffer size depends on the conv/fc layers
            elif layer_type == 'element_sum':

                tmp_tileinfo['type'] = 'element_sum'
                tmp_tileinfo['mx'] = 0
                tmp_tileinfo['my'] = 0
                tmp_tileinfo['max_row'] = 0
                tmp_tileinfo['max_column'] = 0
                tmp_tileinfo['max_group'] = 0
                if 'Outputindex' not in layer_dict.keys():
                    tmp_tileinfo['Outputindex'] = [1]
                else:
                    tmp_tileinfo['Outputindex'] = list(map(int, layer_dict['Outputindex']))
                # Outputindex: the relative index of the output layers of this layer
                if len(tmp_tileinfo['Outputindex']) == 1:
                    tmp_tileinfo['is_branchin'] = -1
                else:
                    tmp_tileinfo['is_branchin'] = 1
                tmp_tileinfo['is_branchout'] = -1
                # is_branchin: if this layer is the input layer of a branch
                Inputindex_list = list(map(int, layer_dict['Inputindex']))
                tmp_tileinfo['Inputindex'] = Inputindex_list
                assert len(Inputindex_list)>1, "the number of element_sum's previous layers must > 1"
                idx = 0
                previous_layer_dict = self.net[layer_id + Inputindex_list[0]][0][0]
                while previous_layer_dict['type'] == 'element_sum':
                    idx = idx+1
                    previous_layer_dict = self.net[layer_id + Inputindex_list[idx]][0][0]
               
                previous_output_size = list(map(int, previous_layer_dict['Outputsize']))
                tmp_tileinfo['datanum_branchout'] = previous_layer_dict['Outputchannel']
                    # the data number of each branch output, assume the previous layer generates 1*1*outputchannel each cycle
                tmp_tileinfo['bit_branchout'] = previous_layer_dict['outputbit']
                    # the data precision of each branch output (bit)
                data_size = tmp_tileinfo['datanum_branchout']*tmp_tileinfo['bit_branchout']*len(Inputindex_list)/8
                    # unit: Byte
                self.global_data_size = self.global_data_size + data_size
                self.global_buf_size = self.global_buf_size + math.pow(2,math.ceil(math.log(data_size,2)))/1024
                    # unit: KB
                self.global_adder_num = self.global_adder_num + previous_layer_dict['Outputchannel']*len(Inputindex_list)//2
                if tmp_tileinfo['bit_branchout']>self.global_adder_bitwidth:
                    self.global_adder_bitwidth = tmp_tileinfo['bit_branchout']
            elif layer_type == 'element_multiply':
                tmp_tileinfo['type'] = 'element_multiply'
                tmp_tileinfo['mx'] = 0
                tmp_tileinfo['my'] = 0
                tmp_tileinfo['max_row'] = 0
                tmp_tileinfo['max_column'] = 0
                tmp_tileinfo['max_group'] = 0
                if 'Outputindex' not in layer_dict.keys():
                    tmp_tileinfo['Outputindex'] = [1]
                else:
                    tmp_tileinfo['Outputindex'] = list(map(int, layer_dict['Outputindex']))
                # Outputindex: the relative index of the output layers of this layer
                if len(tmp_tileinfo['Outputindex']) == 1:
                    tmp_tileinfo['is_branchin'] = -1
                else:
                    tmp_tileinfo['is_branchin'] = 1
                tmp_tileinfo['is_branchout'] = -1
                # is_branchin: if this layer is the input layer of a branch
                Inputindex_list = list(map(int, layer_dict['Inputindex']))
                tmp_tileinfo['Inputindex'] = Inputindex_list
                assert len(Inputindex_list)>1, "the number of element_multiply's previous layers must > 1"
                idx = 0
                previous_layer_dict = self.net[layer_id + Inputindex_list[0]][0][0]
                while previous_layer_dict['type'] == 'element_multiply':
                    idx = idx+1
                    previous_layer_dict = self.net[layer_id + Inputindex_list[idx]][0][0]
                #previous_output_size = list(map(int, previous_layer_dict['Outputsize']))
                tmp_tileinfo['datanum_branchout'] = previous_layer_dict['Outputchannel']
                    # the data number of each branch output, assume the previous layer generates 1*1*outputchannel each cycle
                tmp_tileinfo['bit_branchout'] = previous_layer_dict['outputbit']
                    # the data precision of each branch output (bit)
                data_size = tmp_tileinfo['datanum_branchout']*tmp_tileinfo['bit_branchout']*len(Inputindex_list)/8
                    # unit: Byte
                self.global_data_size = self.global_data_size + data_size
                self.global_buf_size = self.global_buf_size + math.pow(2,math.ceil(math.log(data_size,2)))/1024
                    # unit: KB
                self.global_multiplier_num = self.global_multiplier_num + previous_layer_dict['Outputchannel']*len(Inputindex_list)//2
                if tmp_tileinfo['bit_branchout']>self.global_adder_bitwidth:
                    self.global_adder_bitwidth = tmp_tileinfo['bit_branchout']
            if layer_type == 'conv' or layer_type == 'fc':
                total_xbar_num += tmp_tileinfo['mx'] * tmp_tileinfo['my'] * multiple[layer_id]  # 需要的xbar总数量（一对正负xbar视为一个）
            tmp_tileinfo['PEnum'] = tmp_tileinfo['mx'] * tmp_tileinfo['my'] * multiple[layer_id] # 需要的PE的数量
            num.append(tmp_tileinfo['PEnum']) # 该层所需的PE的数量
            tmp_tileinfo['tilenum'] = math.ceil(tmp_tileinfo['PEnum'] / self.tile.tile_PE_total_num) # 该层需要的tile的总数量
            tmp_tileinfo['max_PE'] = min(tmp_tileinfo['PEnum'], self.tile.tile_PE_total_num)
            start_tileid += tmp_tileinfo['tilenum'] # 更新下一量化层映射的tile_id
            self.layer_tileinfo.append(tmp_tileinfo) # 在tile信息列表中添加该层的tile信息

            inputbit = int(layer_dict['Inputbit'])
            if tmp_tileinfo['type'] == 'conv' or tmp_tileinfo['type'] == 'fc':
                # pe的输入缓存，单位KB
                # 如果该层是卷积层
                # data_inbuf = input_size_list[1] * int(layer_dict['Kernelsize']) * inputchannel * int(layer_dict['Inputbit'])/8
                tmp_inbuf_size = math.pow(2,math.ceil(math.log(data_inbuf / tmp_tileinfo['PEnum'],2)))/1024
                # tile的输出缓存，单位KB
                # data_outbuf = outputchannel*int(layer_dict['outputbit'])/8
                tmp_outbuf_size = math.pow(2,math.ceil(math.log(data_outbuf*2 / tmp_tileinfo['tilenum'],2)))/1024 # 2: ping-pong
            else:
                tmp_inbuf_size = 0
                tmp_outbuf_size = 0
            # unit: KB, restricted in 2^M KB
            if tmp_inbuf_size > self.max_inbuf_size:
                self.max_inbuf_size = tmp_inbuf_size
            if tmp_outbuf_size > self.max_outbuf_size:
                self.max_outbuf_size = tmp_outbuf_size

        self.used_tile_num = start_tileid
        assert self.used_tile_num <= self.tile_total_num, "Tile number is not enough"
            # TODO: update weight rewrite in xbar
        print("Total crossbar number:", total_xbar_num)
        self.inLayer_distance = np.zeros([1, self.layer_num])
        self.transLayer_distance = np.zeros([1, self.layer_num])
        self.aggregate_arg = np.zeros([self.layer_num, 2])

    def mapping_matrix_gen(self):
        if self.tile_connection == 0:
            self.mapping_order = generate_normal_matrix(self.mapping_order.shape[0], self.mapping_order.shape[1])
        elif self.tile_connection == 1:
            self.mapping_order = generate_snake_matrix(self.mapping_order.shape[0], self.mapping_order.shape[1])
        elif self.tile_connection == 2: # 默认使用
            self.mapping_order = generate_hui_matrix(self.mapping_order.shape[0], self.mapping_order.shape[1])
        elif self.tile_connection == 3:
            self.mapping_order = generate_zigzag_matrix(self.mapping_order.shape[0], self.mapping_order.shape[1])

    def mapping_net(self):
        self.mapping_matrix_gen()
        for i in range(self.mapping_order.shape[0]):
            for j in range(self.mapping_order.shape[1]):
                if self.mapping_order[i][j] < self.used_tile_num:
                    for layer_id in range(self.layer_num - 1):
                        if self.layer_tileinfo[layer_id]['type'] in ['conv','pooling','fc']:
                            # only allocate tile for conv layers, pooling layers, and fc layers
                            if ((self.mapping_order[i][j] >= self.layer_tileinfo[layer_id]['startid']) &
                                    (self.mapping_order[i][j] < self.layer_tileinfo[layer_id + 1]['startid'])):
                                self.mapping_result[i][j] = layer_id
                                break
                            elif self.mapping_order[i][j] >= self.layer_tileinfo[self.layer_num - 1]['startid']:
                                self.mapping_result[i][j] = self.layer_num - 1

    def calculate_transfer_distance(self):
        for layer_id in range(self.layer_num - 1):
            # Determine the aggregate node for layer 0~N-1
            if self.layer_tileinfo[layer_id]['is_branchout'] == 1: 
                # 每一层选择层内距离和层间距离最小的tile作为聚合结点
                # for the layer which is a output layer of one branch and the next layer is element_sum
                if self.layer_tileinfo[layer_id]['type'] in ['conv', 'pooling', 'fc']:
                    src_pos = np.argwhere(self.mapping_result == layer_id)
                    if len(src_pos) == 1:
                        self.inLayer_distance[0][layer_id] = 0
                        self.aggregate_arg[layer_id] = src_pos[0]
                        self.transLayer_distance[0][layer_id] = abs(src_pos[0][0]-1/2*self.tile_num[0]) + src_pos[0][1]
                    else:
                        mindis_total = 1000
                        for A in range(len(src_pos)):
                            tmp_transLayer_distance = abs(src_pos[A][0]-1/2*self.tile_num[0]) + src_pos[A][1]
                            maxdis_in = 0
                            for i in range(len(src_pos)):
                                if i != A:
                                    dis_in = abs(src_pos[A][0] - src_pos[i][0]) + abs(src_pos[A][1] - src_pos[i][1])
                                    if dis_in > maxdis_in:
                                        maxdis_in = dis_in
                            if (maxdis_in+tmp_transLayer_distance)<mindis_total:
                                self.inLayer_distance[0][layer_id] = maxdis_in
                                self.transLayer_distance[0][layer_id] = tmp_transLayer_distance
                                self.aggregate_arg[layer_id] = src_pos[A]
                                mindis_total = maxdis_in+tmp_transLayer_distance
            else:
                if self.layer_tileinfo[layer_id]['type'] in ['conv', 'pooling', 'fc']:
                    src_pos = np.argwhere(self.mapping_result == layer_id)
                    if len(src_pos) == 1:
                        self.inLayer_distance[0][layer_id] = 0
                        self.aggregate_arg[layer_id] = src_pos[0]
                        maxdis = 0
                        for idx in self.layer_tileinfo[layer_id]['Outputindex']:
                            dst_pos = np.argwhere(self.mapping_result == (layer_id + idx))
                            for i in range(len(dst_pos)):
                                dis = abs(src_pos[0][0] - dst_pos[i][0]) + abs(src_pos[0][1] - dst_pos[i][1])
                                if dis > maxdis:
                                    maxdis = dis
                        self.transLayer_distance[0][layer_id] = maxdis
                    else:
                        mindis_total = 1000
                        for A in range(len(src_pos)):
                            maxdis_in = 0
                            maxdis_out = 0
                            for i in range(len(src_pos)):
                                if i != A:
                                    dis_in = abs(src_pos[A][0] - src_pos[i][0]) + abs(src_pos[A][1] - src_pos[i][1])
                                    if dis_in > maxdis_in:
                                        maxdis_in = dis_in
                            for idx in self.layer_tileinfo[layer_id]['Outputindex']:
                                dst_pos = np.argwhere(self.mapping_result == (layer_id + idx))
                                for j in range(len(dst_pos)):
                                    dis_out = abs(src_pos[A][0] - dst_pos[j][0]) + abs(src_pos[A][1] - dst_pos[j][1])
                                    if dis_out > maxdis_out:
                                        maxdis_out = dis_out
                            tempdis = maxdis_in + maxdis_out
                            if tempdis < mindis_total:
                                self.inLayer_distance[0][layer_id] = maxdis_in
                                self.transLayer_distance[0][layer_id] = maxdis_out
                                self.aggregate_arg[layer_id] = src_pos[A]
                                mindis_total = tempdis
                elif self.layer_tileinfo[layer_id]['type'] == 'element_sum' or self.layer_tileinfo[layer_id]['type'] == 'element_multiply':
                    maxdis_out = 0
                    for idx in self.layer_tileinfo[layer_id]['Outputindex']:
                        dst_pos = np.argwhere(self.mapping_result == (layer_id + idx))
                        for j in range(len(dst_pos)):
                            dis_out = abs(dst_pos[0][0]-1/2*self.tile_num[0]) + dst_pos[0][1]
                            if dis_out > maxdis_out:
                                maxdis_out = dis_out
                    self.inLayer_distance[0][layer_id] = 0
                    self.transLayer_distance[0][layer_id] = maxdis_out
        final_pos = np.argwhere(self.mapping_result == self.layer_num - 1)
        # Determine the aggregate node for layer N (output layer)
        mindis = 1000
        for i in range(len(final_pos)):
            maxdis = 0
            for j in range(len(final_pos)):
                if j != i:
                    dis = abs(final_pos[i][0] - final_pos[j][0]) + abs(final_pos[i][1] - final_pos[j][1])
                    if dis > maxdis:
                        maxdis = dis
            if maxdis < mindis:
                mindis = maxdis
                self.inLayer_distance[0][self.layer_num - 1] = mindis
                self.aggregate_arg[self.layer_num - 1] = final_pos[i]
                self.transLayer_distance[0][self.layer_num - 1] = 0
        # self.total_distance = sum(sum(self.trans_time * (self.inLayer_distance + self.transLayer_distance)))

# 仅考虑Group_Num=1且Xbar_Polarity=2的情况
class TCG_OU(TCG):
    def __init__(self, NetStruct, SimConfig_path, multiple=None):
        super().__init__(NetStruct, SimConfig_path, multiple)
        # # 权重组织分析
        # print(len(self.net)) # 层数   self.net：整个网络权重
        # print(len(self.net[0])) # 第0层的tile数量   self.net[0]：第0层所有tile
        # # self.net[0][0]：第0层的第0个tile（是一个元组，第一个元素self.net[0][0][0]是该层的信息，第二个元素self.net[0][0][1]是该tile的所有PE）
        # print(len(self.net[0][0][1])) # 第0层的第0个tile中的PE数量 self.net[0][0][1]：第0层第0个tile中的所有PE
        # print(len(self.net[0][0][1][0])) # 第0层的第0个tile中的第0个PE中含有的xbar组数（此处等于权重比特数8,应该作修改） self.net[0][0][1][0]：第0层第0个tile的第0个PE
        # # print(self.net[0][0][1][0][0]) 第0层的第0个tile中的第0个PE中的第0位权重值，长度为2，第一个元素为正权重，第二个元素为负权重
        # print(self.net[0][0][1][0][0][0].shape) # 第0层的第0个tile中的第0个PE中的第0位的正权重的形状 (列，行)(128,27)

        for layer_id in range(self.layer_num):
            layer_dict = self.net[layer_id][0][0]
            layer_type = layer_dict['type']
            inputbit = int(layer_dict['Inputbit'])
            max_ou_cycle = 0
            ou_cycle_sum = 0
            pe_num = 0 # 该层pe数量

            # 每一个xbar的OU计算次数组织 ou_cycle_array[0]:该层第0个tile次数组织
            # ou_cycle_array[0][0]：该层第0个tile的第0个PE次数组织
            # ou_cycle_array[0][0][0]：该层第0个tile的第0个PE的正权重xbar上的计算次数
            # ou_cycle_array[0][0][1]：该层第1个tile的第1个PE的负权重xbar上的计算次数
            ou_cycle_array = [] # 长度为tile数
            
            if layer_type == 'conv' or layer_type == 'fc':
                for tile_id in range(len(self.net[layer_id])):
                    tile_ou_cycle = [] # 长度为该tile的PE数
                    for pe_id in range(len(self.net[layer_id][tile_id][1])):
                        pe_num += 8
                        for group_id in range(len(self.net[layer_id][tile_id][1][pe_id])):
                            pe_ou_cycle = [] # 长度为2
                            for sign_id in range(len(self.net[layer_id][tile_id][1][pe_id][group_id])):
                                xbar_weight = self.net[layer_id][tile_id][1][pe_id][group_id][sign_id]
                                xbar_ou_cycle = inputbit * math.ceil(xbar_weight.shape[1] / self.tile.ou_row) * \
                                        math.ceil(xbar_weight.shape[0] / self.tile.ou_column)
                                if xbar_ou_cycle > max_ou_cycle:
                                    max_ou_cycle = xbar_ou_cycle
                                pe_ou_cycle.append(xbar_ou_cycle)
                                ou_cycle_sum += xbar_ou_cycle
                            tile_ou_cycle.append(pe_ou_cycle)
                    ou_cycle_array.append(tile_ou_cycle)
                
                self.layer_tileinfo[layer_id]['max_OU_cycle'] = max_ou_cycle #最大ou计算次数的xbar的ou计算次数（以xbar为单位）
                self.layer_tileinfo[layer_id]['OU_cycle_array'] = ou_cycle_array
                self.layer_tileinfo[layer_id]['OU_cycle_sum'] = ou_cycle_sum
                self.layer_tileinfo[layer_id]['PE_num'] = pe_num
                self.layer_tileinfo[layer_id]['avg_OU_cycle_PE'] = ou_cycle_sum / pe_num #pe的平均ou计算次数（以pe为单位）

        # OU计算次数列表的测试
        # print(len(self.layer_tileinfo[0]['OU_cycle_array'])) # 第0层的tile数
        # print(len(self.layer_tileinfo[0]['OU_cycle_array'][0])) # 第0层中第0个tile上的PE数
        # print(len(self.layer_tileinfo[0]['OU_cycle_array'][0][0])) #2
        # print(self.layer_tileinfo[0]['OU_cycle_array'][0][0][0]) # 第0层中第0个tile上的正权重阵列所需要的OU计算次数
        # print(self.layer_tileinfo[0]['max_OU_cycle']) # 第0层中最大的OU计算次数的pe的OU计算次数

        #平均OU计算次数、OU计算次数和、每层pe数测试
        # print(self.layer_tileinfo[0]['PE_num'])
        # print(self.layer_tileinfo[0]['OU_cycle_sum'])
        # print(self.layer_tileinfo[0]['avg_OU_cycle_PE'])
       

if __name__ == '__main__':
    test_SimConfig_path = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), "SimConfig.ini")
    test_weights_file_path = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())),
                                          "cifar10_vgg8_params.pth")

    __TestInterface = TrainTestInterface('vgg8', 'MNSIM.Interface.cifar10', test_SimConfig_path,
                                         test_weights_file_path, 'cpu')
    structure_file = __TestInterface.get_structure()

    # test = TCG(structure_file, test_SimConfig_path)
    test = TCG_OU(structure_file, test_SimConfig_path)
    test.mapping_net()
    test.calculate_transfer_distance()
    # print(test.total_distance)
