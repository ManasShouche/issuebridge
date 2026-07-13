from slack_bolt.async_app import AsyncApp

from .feedback_buttons import handle_feedback_button
from .triage_buttons import (
    handle_dismiss_triage,
    handle_draft_submit,
    handle_post_reply,
    handle_show_draft,
)


def register(app: AsyncApp):
    app.action("feedback")(handle_feedback_button)
    app.action("post_reply")(handle_post_reply)
    app.action("show_draft")(handle_show_draft)
    app.action("dismiss_triage")(handle_dismiss_triage)
    app.view("draft_modal_submit")(handle_draft_submit)
