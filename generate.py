import sys
import os
import re
from datetime import datetime

from dotenv import load_dotenv
from anthropic import Anthropic

PROMPT_TEMPLATE = (
    "You are writing for a small business owner who has 5 minutes between "
    "tasks and is skeptical of AI hype. Topic: {topic}.\n\n"
    "Write a short Markdown article (a title heading plus 3-4 sections) that "
    "helps them use AI tools or AI automation to save time, make money, or "
    "kill annoying busywork related to this topic.\n\n"
    "Requirements:\n"
    "- Open with a specific, concrete scenario (a moment, a number, a "
    "recognizable frustration) instead of a general statement about AI or "
    "small businesses.\n"
    "- Name real, current tools by name, and say exactly what to do with "
    "them (a setting to change, a prompt to type, a workflow to set up) — "
    "not just what category of tool exists.\n"
    "- Include at least one realistic number: time saved, cost, a "
    "percentage, or a rough dollar figure. If you're estimating, say so "
    "plainly rather than presenting it as measured fact.\n"
    "- Write like a knowledgeable person talking to a peer: plain words, "
    "varied sentence length, no corporate throat-clearing ('In today's "
    "fast-paced world...'), no empty superlatives ('game-changing', "
    "'revolutionize', 'unlock'), and no meaningless summary line at the end "
    "that just restates the title.\n"
    "- Mention one real limitation, cost, or failure mode of the AI "
    "approach you're recommending — don't oversell it.\n"
    "- Skip generic advice a reader has already heard a dozen times; if a "
    "tip isn't specific to this topic, cut it."
)

def main():
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: python generate.py <topic>")
        sys.exit(1)

    topic = " ".join(sys.argv[1:])
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()

    if os.environ.get("TEST_MODE") == "1":
        content = (
            f"# [MOCK RESPONSE] {topic}\n\n"
            f"This is a locally generated mock response for testing. "
            f"No Anthropic API call was made.\n"
        )
    elif provider == "gemini":
        from google import genai

        client = genai.Client()  # reads GEMINI_API_KEY from the environment

        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=PROMPT_TEMPLATE.format(topic=topic),
        )

        content = response.text
    else:
        client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment

        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": PROMPT_TEMPLATE.format(topic=topic),
                }
            ],
        )

        content = message.content[0].text

    os.makedirs("output", exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_topic = re.sub(r"[^a-zA-Z0-9_-]+", "_", topic.strip().lower())
    filename = f"output/{safe_topic}_{timestamp}.md"

    with open(filename, "w") as f:
        f.write(content)

    print(f"Saved to {filename}")

if __name__ == "__main__":
    main()
