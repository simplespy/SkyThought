#!/bin/bash

SKYT_HOME="/ephemeral/peiyao-workspace/SkyThought"
OUTPUT_LOG="test-log.txt"
MODEL="Qwen/QwQ-32B-Preview"
#MODEL="Qwen/QwQ-32B-Preview""deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
#Qwen/Qwen2.5-1.5B-Instruct" 

#CUDA_VISIBLE_DEVICES=2,3,4,7 
nohup python inference_and_check.py \
    --task taco \
    --model "$MODEL"\
    --tp 8 \
    --split train \
    --difficulty MEDIUM \
    --max_tokens 4096 \
    --result-dir $SKYT_HOME/data \
    --inference > $OUTPUT_LOG 2>&1 &


