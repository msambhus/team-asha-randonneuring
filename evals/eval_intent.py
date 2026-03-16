"""Intent classification accuracy eval (EVAL-02).

Measures whether classify_intent() correctly identifies user intent across
all 6 intent types. Dataset has 20+ labeled randonneuring-specific messages.

Run: braintrust eval evals/eval_intent.py
  or: python evals/eval_intent.py
"""
import os
import sys

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from braintrust import Eval, init_dataset
from openai import OpenAI

from services.chat_service import classify_intent

# 28 labeled records — at least 4 per intent type
INTENT_DATASET_RECORDS = [
    # data_query (5 records)
    {"input": {"question": "What is my fitness score?"}, "expected": "data_query"},
    {"input": {"question": "How many brevets have I finished?"}, "expected": "data_query"},
    {"input": {"question": "Show my upcoming rides"}, "expected": "data_query"},
    {"input": {"question": "What are my career stats?"}, "expected": "data_query"},
    {"input": {"question": "List my recent Strava activities"}, "expected": "data_query"},

    # coaching (5 records)
    {"input": {"question": "How should I train for a 400km brevet?"}, "expected": "coaching"},
    {"input": {"question": "Am I ready for a 600km?"}, "expected": "coaching"},
    {"input": {"question": "What pace should I target for a 300?"}, "expected": "coaching"},
    {"input": {"question": "How to build base miles for randonneuring?"}, "expected": "coaching"},
    {"input": {"question": "Can you suggest a training plan for my next brevet?"}, "expected": "coaching"},

    # knowledge (5 records)
    {"input": {"question": "What are ACP time limits for brevets?"}, "expected": "knowledge"},
    {"input": {"question": "What is a Super Randonneur?"}, "expected": "knowledge"},
    {"input": {"question": "How does R-12 work?"}, "expected": "knowledge"},
    {"input": {"question": "What should I eat on a 600km brevet?"}, "expected": "knowledge"},
    {"input": {"question": "What are the official brevet distances?"}, "expected": "knowledge"},

    # web_search (4 records)
    {"input": {"question": "What's a good bike for randonneuring under $2000?"}, "expected": "web_search"},
    {"input": {"question": "Is the Shimano 105 groupset good for brevets?"}, "expected": "web_search"},
    {"input": {"question": "Schwalbe Marathon vs Continental Gatorskin for long rides?"}, "expected": "web_search"},
    {"input": {"question": "Best dynamo hub for randonneuring?"}, "expected": "web_search"},

    # route_discussion (5 records)
    {"input": {"question": "Tell me about the Cascade 400 route"}, "expected": "route_discussion"},
    {"input": {"question": "What are the control stops for the Fleche?"}, "expected": "route_discussion"},
    {"input": {"question": "How much elevation on the SIR 300?"}, "expected": "route_discussion"},
    {"input": {"question": "Describe the Cascade 200 route"}, "expected": "route_discussion"},
    {"input": {"question": "What is the ride plan for the Cougar Mountain 200?"}, "expected": "route_discussion"},

    # off_topic (4 records)
    {"input": {"question": "Who won the World Cup?"}, "expected": "off_topic"},
    {"input": {"question": "Best pizza in Seattle?"}, "expected": "off_topic"},
    {"input": {"question": "Tell me a joke about cats"}, "expected": "off_topic"},
    {"input": {"question": "What's the weather in Paris?"}, "expected": "off_topic"},
]


def intent_accuracy_scorer(input, output, expected, metadata=None):
    """Score 1 if classified intent matches expected, 0 otherwise."""
    return {
        "name": "intent_accuracy",
        "score": 1 if output == expected else 0,
        "metadata": {"expected": expected, "got": output},
    }


def classify_task(input):
    """Task function: classify a user message and return the intent string."""
    client = OpenAI()
    result, _usage = classify_intent(client, input["question"], [])
    return result.intent


def seed_and_run():
    """Seed the Braintrust dataset and run the eval."""
    dataset = init_dataset(project="Team Asha", name="intent_classification")
    for record in INTENT_DATASET_RECORDS:
        dataset.insert(
            input=record["input"],
            expected=record["expected"],
            metadata={"intent_type": record["expected"]},
        )

    Eval(
        "Team Asha",
        experiment_name="intent_classification_baseline",
        data=lambda: INTENT_DATASET_RECORDS,
        task=classify_task,
        scores=[intent_accuracy_scorer],
    )


if __name__ == "__main__":
    seed_and_run()
