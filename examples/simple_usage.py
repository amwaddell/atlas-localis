"""Use the same local application as UI, CLI and evaluation."""

from workbench.graphs.research_models import ConversationInput
from workbench.systems.runner import create_application


def main():
    with create_application() as application:
        result = application.invoke(
            ConversationInput(question="When did the Roman Republic end?")
        )
        print(result.answer_text)
        print(result.outcome, result.run_id)


if __name__ == "__main__":
    main()
