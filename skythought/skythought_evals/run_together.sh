#!/bin/bash

SKYT_HOME="/ephemeral/peiyao-workspace/SkyThought"

MODELS=("Qwen/QwQ-32B-Preview" "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B")
TASKS=("atcoder" "supervised_atcoder")

START=0
END=-1

for MODEL in "${MODELS[@]}"; do
    MODEL_SHORT=$(echo "$MODEL" | awk -F'/' '{print $2}')  

    for TASK in "${TASKS[@]}"; do
        OUTPUT_LOG_INF="logs/${MODEL_SHORT}_${TASK}_${START}_${END}_inference.log"
        OUTPUT_LOG_CHECK="logs/${MODEL_SHORT}_${TASK}_${START}_${END}_check.log"

        echo "Running INFERENCE: Model=$MODEL, Task=$TASK, Log=$OUTPUT_LOG_INF"

        python inference_and_check.py \
            --task "$TASK" \
            --model "$MODEL" \
            --tp 8 \
            --start "$START" \
            --end "$END" \
            --max_tokens 8192 \
            --temperatures 1 \
            --n 4 \
            --result-dir "$SKYT_HOME/data" \
            --inference > "$OUTPUT_LOG_INF" 2>&1

        echo "INFERENCE completed for Model=$MODEL, Task=$TASK."

        echo "Running CHECK: Model=$MODEL, Task=$TASK, Log=$OUTPUT_LOG_CHECK"

        python inference_and_check.py \
            --task "$TASK" \
            --model "$MODEL" \
            --tp 8 \
            --start "$START" \
            --end "$END" \
            --max_tokens 8192 \
            --temperatures 1 \
            --n 4 \
            --result-dir "$SKYT_HOME/data" \
            --check > "$OUTPUT_LOG_CHECK" 2>&1

        echo "CHECK completed for Model=$MODEL, Task=$TASK."

    done
done

echo "All inference and check jobs completed!"
