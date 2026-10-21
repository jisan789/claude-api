import sys
from perplexity_chat import run_perplexity_bot, parser

if __name__ == "__main__":
    print("[Note] System migrated from Claude to Perplexity AI.")
    args = parser.parse_args()
    run_perplexity_bot(invisible=not args.visible, initial_prompt=args.prompt)
