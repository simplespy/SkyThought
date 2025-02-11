import argparse
import concurrent.futures
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial
from torch import float16
import numpy as np
from openai import OpenAI
from skythought_evals.tasks import (
    TASK_HANDLER_MAP,
    NUMINATaskHandler,
    TaskConfig,
    TaskHandler,
)
from skythought_evals.tasks.task_util import get_tasks
from skythought_evals.util.common import set_seed
from skythought_evals.util.model_utils import MODEL_TO_NAME, SYSTEM_PROMPT
from tqdm import tqdm
from vllm import LLM, SamplingParams

module_dir = os.path.dirname(os.path.abspath(__file__))
TASK_NAMES_TO_YAML = get_tasks(os.path.join(module_dir, "tasks"))


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def fetch_response_openai(llm, model_name, max_tokens, temp, prompt):
    model_name = model_name.replace("openai/", "")
    if "o1" in model_name:
        # O1 doesn't support system prompt
        # NOTE: might want to implement this inside handler instead
        for p in prompt:
            p["role"] = "user"

        response = llm.chat.completions.create(
            model=model_name,
            messages=prompt,
            n=1,
            temperature=1,  # has to be 1
            max_completion_tokens=max_tokens,
        )
    else:
        response = llm.chat.completions.create(
            model=model_name,
            messages=prompt,
            n=1,
            temperature=temp,
            max_tokens=max_tokens,
        )
    return response


def perform_inference_and_check(
    handler: TaskHandler,
    temperatures,
    max_tokens,
    result_file,
    llm,
    system_prompt,
    args,
):
    results = handler.load_existing_results(result_file)
    print(f"Loaded {len(results)} existing results.")
    train_data = handler.load_and_filter_dataset(
        args.start,
        args.end,
        split=args.split,
        subset=args.subset,
        difficulty=args.difficulty,
        args=args,
    )
    remaining_data = handler.process_remaining_data(train_data, results)
    conversations = handler.make_conversations(
        remaining_data, system_prompt, args.model
    )
    for temp in temperatures:
        if args.model.startswith("openai"):
            fetch_partial = partial(
                fetch_response_openai, llm, args.model, max_tokens, temp
            )

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as e:
                responses = list(e.map(fetch_partial, conversations))

        else:
            sampling_params = SamplingParams(max_tokens=max_tokens, temperature=temp)
            responses = llm.chat(
                messages=conversations, sampling_params=sampling_params, use_tqdm=True
            )

        total_correct = 0
        total_finish = 0
        with ProcessPoolExecutor(max_workers=32) as executor:
            # future_to_task = {
            #     executor.submit(handler.update_results, remaining_data[idx], response): idx
            #     for idx, response in enumerate(responses)
            # }
            future_to_task = {}
            token_usages = {}
            for idx, response in enumerate(responses):
                if args.model.startswith("openai"):
                    response_str = response.choices[0].message.content.strip()
                else:
                    response_str = response.outputs[0].text.strip()
                future_to_task[
                    executor.submit(
                        handler.update_results, remaining_data[idx], response_str
                    )
                ] = idx
                # print(f"Request output: {response}")

                if args.model.startswith("openai"):
                    token_usages[idx] = response.usage
                else:
                    token_usages[idx] = {
                        "completion_tokens": len(response.outputs[0].token_ids),
                        "prompt_tokens": len(response.prompt_token_ids),
                    }

            for future in tqdm(
                as_completed(future_to_task),
                total=len(future_to_task),
                desc="Processing Generations",
            ):
                idx = future_to_task[future]
                response_entry = future.result()
                total_correct += response_entry["correctness"]
                total_finish += 1

                problem_key = remaining_data[idx][handler.question_key]
                if problem_key not in results:
                    results[problem_key] = remaining_data[idx]
                    if isinstance(handler, NUMINATaskHandler):
                        results[problem_key]["messages"] = ""
                    results[problem_key]["responses"] = {}
                    results[problem_key]["token_usages"] = {}
                    prompt = conversations[idx][1]["content"]
                    results[problem_key]["prompt"] = prompt
                    results[problem_key]["input_conversation"] = conversations[idx]

                results[problem_key]["responses"][str(temp)] = response_entry

                if args.model.startswith("openai"):
                    results[problem_key]["token_usages"][str(temp)] = {
                        "completion_tokens": token_usages[idx].completion_tokens,
                        "prompt_tokens": token_usages[idx].prompt_tokens,
                    }
                else:
                    # TODO: vLLM model, can it do the same thing
                    results[problem_key]["token_usages"][str(temp)] = token_usages[idx]

        print(f"Final acc: {total_correct}/{total_finish}")
        acc = round(total_correct / total_finish, 4) if total_finish > 0 else 0
        print(json.dumps({"acc": acc}))

    completion_tokens = [
        results[key]
        .get("token_usages", {})
        .get(str(temp), {})
        .get("completion_tokens", 0)
        for key in results
        for temp in temperatures
    ]
    prompt_tokens = [
        results[key].get("token_usages", {}).get(str(temp), {}).get("prompt_tokens", 0)
        for key in results
        for temp in temperatures
    ]

    # Token usage summary
    result_dir, result_name = os.path.split(result_file)
    token_usage_dir = os.path.join(result_dir, "token_usage")
    os.makedirs(token_usage_dir, exist_ok=True)

    # Construct the token usage result file path
    token_usage_result_file = os.path.join(token_usage_dir, result_name)

    # Prepare the token usage dictionary
    token_dict = {
        "completion_tokens": sum(completion_tokens),
        "prompt_tokens": sum(prompt_tokens),
        "avg_completion_tokens": (
            round(sum(completion_tokens) / len(completion_tokens), 3)
            if completion_tokens
            else 0
        ),
        "avg_prompt_tokens": (
            round(sum(prompt_tokens) / len(prompt_tokens), 3) if prompt_tokens else 0
        ),
    }

    # Save the token usage dictionary to the result file
    with open(token_usage_result_file, "w") as f:
        json.dump(token_dict, f, indent=4)

    print(f"Token usage saved to {token_usage_result_file}")

    with open(result_file, "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=4, cls=NumpyEncoder)


def perform_check(handler: TaskHandler, temperatures, result_file, args):
    results = handler.load_existing_results(result_file)
    print(f"Loaded {len(results)} existing results.")

    train_data = handler.load_and_filter_dataset(
        args.start,
        args.end,
        split=args.split,
        subset=args.subset,
        difficulty=args.difficulty,
        args=args,
    )
    remaining_data = handler.process_remaining_data(train_data, {})

    tasks = []
    for item in remaining_data:
        problem_key = item[handler.question_key]
        print("problem_key", problem_key)
        # If this item exists in the results file, check each temperature
        if problem_key in results and "responses" in results[problem_key]:
            for temp in temperatures:
                print("temp", str(temp))
                if str(temp) in results[problem_key]["responses"]:
                    
                    response_entries = results[problem_key]["responses"][str(temp)]
                    print("response_entries", response_entries)
                    for sample_id, response_entry in enumerate(response_entries):
                        if sample_id > (args.n - 1):
                            continue
                        if True or response_entry["correctness"] is None:
                            processed = "processed_content" in response_entry
                            tasks.append(
                                (
                                    item,
                                    temp,
                                    (
                                        response_entry["processed_content"]
                                        if processed
                                        else response_entry["content"]
                                    ),
                                    sample_id,
                                )
                            )

    print(f"Found {len(tasks)} responses requiring reject sampling...")

    total_correct = 0
    total_finish = 0
    correct = {temp: {} for temp in temperatures}
    with ProcessPoolExecutor(max_workers=32) as executor:
        future_to_task = {
            executor.submit(handler.update_results, item, content): (
                item,
                temp,
                sample_id,
            )
            for (item, temp, content, sample_id) in tasks
        }

        # 4. Collect the results as they finish.
        for future in tqdm(
            as_completed(future_to_task),
            total=len(future_to_task),
            desc="Processing Reject Sampling",
        ):
            item, temp, sample_id = future_to_task[future]
            new_response_entry = future.result()
            total_correct += new_response_entry["correctness"]
            total_finish += 1

            # Update the corresponding record in results
            problem_key = item[handler.get_question_key()]
            if problem_key not in correct[temp]:
                correct[temp][problem_key] = False
            if new_response_entry["correctness"]:
                correct[temp][problem_key] = True
            assert (
                problem_key in results
                and "responses" in results[problem_key]
                and str(temp) in results[problem_key]["responses"]
            )
            response_entry = results[problem_key]["responses"][str(temp)][sample_id]
            response_entry["correctness"] = new_response_entry["correctness"]
            response_entry["reason"] = new_response_entry["reason"]
            results[problem_key]["responses"][str(temp)][sample_id] = response_entry

    print(f"Final reject-sampling accuracy: {total_correct}/{total_finish}")
    # per temperature acc
    for temp in temperatures:
        temp_correct = sum(correct[temp].values())
        temp_total = len(correct[temp])
        temp_acc = round(temp_correct / temp_total, 4) if temp_total > 0 else 0
        print(f"Temperature {temp} acc: {temp_correct}/{temp_total} ({temp_acc})")

    with open(result_file, "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=4, cls=NumpyEncoder)


def perform_inference_and_save(
    handler: TaskHandler,
    temperatures,
    max_tokens,
    result_file,
    llm,
    system_prompt,
    args,
):
    results = handler.load_existing_results(result_file)
    print(f"Loaded {len(results)} existing results.")
    train_data = handler.load_and_filter_dataset(
        args.start,
        args.end,
        split=args.split,
        subset=args.subset,
        difficulty=args.difficulty,
        args=args,
    )
    remaining_data = handler.process_remaining_data(train_data, results)
    conversations = handler.make_conversations(
        remaining_data, system_prompt, args.model
    )

    for temp in temperatures:
        if args.model.startswith("openai"):
            fetch_partial = partial(
                fetch_response_openai, llm, args.model, max_tokens, temp
            )

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as e:
                responses = list(e.map(fetch_partial, conversations))

        else:
            sampling_params = SamplingParams(
                n=args.n, max_tokens=max_tokens, temperature=temp
            )
            responses = llm.chat(
                messages=conversations, sampling_params=sampling_params, use_tqdm=True
            )

        completion_tokens = []
        prompt_tokens = []
        for idx, response in enumerate(responses):
            response_entries = []
            token_usages = []
            completion_token = 0
            for sample_idx in range(args.n):
                response_entry = {
                    "content": (
                        response.choices[0].message.content.strip()
                        if args.model.startswith("openai")
                        else response.outputs[sample_idx].text.strip()
                    ),
                    "correctness": None,
                    "reason": None,
                }
                response_entries.append(response_entry)
                if not args.model.startswith("openai"):
                    token_usages.append(
                        {
                            "completion_tokens": len(
                                response.outputs[sample_idx].token_ids
                            ),
                            "prompt_tokens": len(response.prompt_token_ids),
                        }
                    )
                    completion_token += len(response.outputs[sample_idx].token_ids)
            completion_token /= args.n
            prompt_token = len(response.prompt_token_ids)
            prompt_tokens.append(prompt_token)
            completion_tokens.append(completion_token)

            problem_key = remaining_data[idx][
                handler.question_key
            ]  # can you use this idx
            if problem_key not in results:
                results[problem_key] = remaining_data[idx]
                if isinstance(handler, NUMINATaskHandler):
                    results[problem_key]["messages"] = ""
                results[problem_key]["responses"] = {}
                results[problem_key]["token_usages"] = {}
                prompt = conversations[idx][1]["content"]
                results[problem_key]["prompt"] = prompt

            results[problem_key]["responses"][str(temp)] = response_entries

            if args.model.startswith("openai"):
                results[problem_key]["token_usages"][str(temp)] = {
                    "completion_tokens": response.usage.completion_tokens,
                    "prompt_tokens": response.usage.prompt_tokens,
                }
            else:
                results[problem_key]["token_usages"][str(temp)] = token_usages

    # Token usage summary put into another subdirectory
    result_dir, result_name = os.path.split(result_file)
    token_usage_dir = os.path.join(result_dir, "token_usage")
    os.makedirs(token_usage_dir, exist_ok=True)

    # Construct the token usage result file path
    token_usage_result_file = os.path.join(token_usage_dir, result_name)

    # Prepare the token usage dictionary
    token_dict = {
        "completion_tokens": sum(completion_tokens),
        "prompt_tokens": sum(prompt_tokens),
        "avg_completion_tokens": (
            round(sum(completion_tokens) / len(completion_tokens), 3)
            if completion_tokens
            else 0
        ),
        "avg_prompt_tokens": (
            round(sum(prompt_tokens) / len(prompt_tokens), 3) if prompt_tokens else 0
        ),
    }

    # Save the token usage dictionary to the result file
    with open(token_usage_result_file, "w") as f:
        json.dump(token_dict, f, indent=4)

    print(f"Token usage saved to {token_usage_result_file}")

    with open(result_file, "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=4, cls=NumpyEncoder)


def main():
    parser = argparse.ArgumentParser(
        description="Unified inference and checking for different datasets/tasks."
    )
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        choices=TASK_NAMES_TO_YAML.keys(),
        help="Task to process.",
    )
    parser.add_argument(
        "--model",
        type=str,
        required=True,
        default="Qwen/QwQ-32B-Preview",
        help="The model to run.",
    )
    parser.add_argument("--tp", type=int, default=8, help="Tensor Parallelism Degree")
    parser.add_argument(
        "--max_tokens", type=int, default=32768, help="Max tokens for the model."
    )
    parser.add_argument(
        "--split",
        type=str,
        default=None,
        help="Split to use for the dataset (e.g., train, test).",
    )
    parser.add_argument("--subset", type=str, help="Subset for the dataset.")
    parser.add_argument("--start", type=int, default=0, help="Start index.")
    parser.add_argument("--end", type=int, default=-1, help="End index.")
    parser.add_argument(
        "--difficulty",
        type=str,
        default=None,
        help="Difficulty level. Example: 'easy', 'medium', 'hard'.",
    )
    parser.add_argument(
        "--filter-difficulty",
        action="store_true",
        help="Optional filter difficulty, used for NUMINA.",
    )
    parser.add_argument(
        "--source",
        type=str,
        help="Source column filter for the dataset, used for NUMINA.",
    )
    parser.add_argument(
        "--result-dir", type=str, default="./", help="Result dir to save files."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Perform evaluation checks on generated samples.",
    )
    parser.add_argument("--inference", action="store_true", help="Perform inference.")
    parser.add_argument(
        "--temperatures",
        type=float,
        nargs="+",
        default=[0],
        help="Temperature for sampling.",
    )
    parser.add_argument(
        "--math-difficulty-lower-bound",
        type=int,
        default=None,
        help="Lowest difficulty level for math.",
    )
    parser.add_argument(
        "--math-difficulty-upper-bound",
        type=int,
        default=None,
        help="Highest difficulty level for math.",
    )
    parser.add_argument(
        "--n", type=int, default=1, help="Number of samples generated per problem."
    )
    parser.add_argument("--seed", type=int, default=41, help="Random seed.")

    args = parser.parse_args()
    set_seed(args.seed)

    if args.task not in TASK_NAMES_TO_YAML:
        raise ValueError(
            f"Task {args.task} not found. Should be one of {TASK_NAMES_TO_YAML.keys()}"
        )

    task_config = TaskConfig.from_yaml(TASK_NAMES_TO_YAML[args.task])
    print(task_config)
    handler_name = task_config.handler
    handler_cls = TASK_HANDLER_MAP[handler_name]
    handler = handler_cls(task_config)

    temperatures = [1] if args.model.startswith("openai/o1") else args.temperatures

    print(f"Temperature: {temperatures}")
    max_tokens = args.max_tokens
    if temperatures == [0] and args.n > 1:
        args.n = 1
        print("Warning: Temperature 0 does not support multiple samples. Setting n=1.")

    # TODO: this can be cleaned up by allowing user override for any task_config with optional task_args
    # Currently kept here for consistency with old code
    args.split = args.split if args.split else handler.task_config.dataset_split
    args.subset = args.subset if args.subset else handler.task_config.dataset_subset
    if not args.difficulty and "difficulty" in handler.task_config.preprocess_config:
        args.difficulty = handler.task_config.preprocess_config["difficulty"]

    # create result dir if not exists
    if args.result_dir and not os.path.exists(args.result_dir):
        os.makedirs(args.result_dir)
    if (
        args.math_difficulty_lower_bound is not None
        or args.math_difficulty_upper_bound is not None
    ):
        result_file = os.path.join(
            args.result_dir,
            f"{MODEL_TO_NAME.get(args.model, args.model.split('/')[-1])}_{args.task}_{args.split}_{args.subset}_{args.filter_difficulty}_{args.start}_{args.end}_{args.math_difficulty_lower_bound}_{args.math_difficulty_upper_bound}.json",
        )
    else:
        result_file = os.path.join(
            args.result_dir,
            f"{MODEL_TO_NAME.get(args.model, args.model.split('/')[-1])}_{args.task}_{args.split}_{args.subset}_{args.filter_difficulty}_{args.start}_{args.end}.json",
        )

    if args.check:
        # check if converted file exists
        if (
            args.math_difficulty_lower_bound is not None
            or args.math_difficulty_upper_bound is not None
        ):
            converted_file = (
                f"{args.result_dir}/converted_{MODEL_TO_NAME.get(args.model, args.model.split('/')[-1])}_{args.task}_{args.split}_{args.subset}_{args.filter_difficulty}_{args.start}_{args.end}"
                + f"_{args.math_difficulty_lower_bound}_{args.math_difficulty_upper_bound}.json"
            )
        else:
            converted_file = f"{args.result_dir}/converted_{MODEL_TO_NAME.get(args.model, args.model.split('/')[-1])}_{args.task}_{args.split}_{args.subset}_{args.filter_difficulty}"
            f"_{args.start}_{args.end}.json"
        if os.path.exists(converted_file):
            result_file = converted_file
        perform_check(handler, temperatures, result_file, args)
        return
    elif args.inference:
        llm = (
            OpenAI()
            if args.model.startswith("openai")
            else LLM(model=args.model, tensor_parallel_size=args.tp)
        )
        system_prompt = SYSTEM_PROMPT.get(args.model, "You are a helpful coding assistant tasked with assisting users in solving technical problems. Your role is to follow a structured reasoning approach before arriving at a precise solution. \n\nYour response should have two sections: Thought and Solution.\n\nIn the **Thought** section, outline your reasoning step by step using this format:\n\n<|begin_of_thought|>\n{Step-by-step logical process, each step separated by '\n\n'}\n<|end_of_thought|>\n\n The thought section should include analysis, summarization, brainstorming, verification, refinement, and backtracking when needed. \n\nIn the **Solution** section, present the final, correct solution concisely and clearly. The code must be complete, compilable, and executable in Python, formatted as:\n\n<|begin_of_solution|>\n{Final Python code solution}\n<|end_of_solution|>\n\nNow, solve the following problem using this framework:")
        perform_inference_and_save(
            handler, temperatures, max_tokens, result_file, llm, system_prompt, args
        )
        return

    llm = (
        OpenAI()
        if args.model.startswith("openai")
        else LLM(model=args.model, tensor_parallel_size=args.tp)
        if args.model != "cognitivecomputations/DeepSeek-R1-AWQ"
        else LLM(model=args.model, tensor_parallel_size=args.tp, trust_remote_code=True, dtype=float16, max_model_len=8192, kv_cache_dtype="fp8_e5m2", calculate_kv_scales=True)
    )
    system_prompt = SYSTEM_PROMPT.get(args.model, "You are a helpful coding assistant tasked with assisting users in solving technical problems. Your role is to follow a structured reasoning approach before arriving at a precise solution. \n\nYour response should have two sections: Thought and Solution.\n\nIn the **Thought** section, outline your reasoning step by step using this format:\n\n<|begin_of_thought|>\n{Step-by-step logical process, each step separated by '\n\n'}\n<|end_of_thought|>\n\n The thought section should include analysis, summarization, brainstorming, verification, refinement, and backtracking when needed. \n\nIn the **Solution** section, present the final, correct solution concisely and clearly. The code must be complete, compilable, and executable in Python, formatted as:\n\n<|begin_of_solution|>\n{Final Python code solution}\n<|end_of_solution|>\n\nNow, solve the following problem using this framework:")

    perform_inference_and_check(
        handler,
        temperatures,
        max_tokens,
        result_file,
        llm,
        system_prompt,
        args,
    )


if __name__ == "__main__":
    main()
