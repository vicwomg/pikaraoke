import logging


class OnScreenNotification:
    """Manages on-screen notifications for the karaoke application."""

    def __init__(self, hide_notifications=False):
        self.hide_notifications = hide_notifications
        self.current_notification = None

    def send(self, message, color="primary"):
        if not self.hide_notifications:
            if self.current_notification is not None:
                return
            self.current_notification = message + "::is-" + color

    def clear(self):
        self.current_notification = None

    def log_and_send(self, message, category="info"):
        # Category should be one of: info, success, warning, danger
        if category == "success":
            logging.info(message)
            self.send(message, "success")
        elif category == "warning":
            logging.warning(message)
            self.send(message, "warning")
        elif category == "danger":
            logging.error(message)
            self.send(message, "danger")
        else:
            logging.info(message)
            self.send(message, "primary")