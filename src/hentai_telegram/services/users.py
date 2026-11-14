class UserService:
    def __init__(self, admin_ids: frozenset[int]):
        self.admin_ids = frozenset(admin_ids)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def require_admin(self, user_id: int):
        if not self.is_admin(user_id):
            raise ValueError("抱歉，本机器人为私有频道管理机器人，仅管理员可用。")
