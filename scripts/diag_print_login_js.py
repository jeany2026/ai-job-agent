from tools.boss_job_search import BOSS_LOGGED_IN_JS

print("LEN", len(BOSS_LOGGED_IN_JS))
print(repr(BOSS_LOGGED_IN_JS))
print("has_backtick", "`" in BOSS_LOGGED_IN_JS)
print("has_dollar_template", "${part}" in BOSS_LOGGED_IN_JS)
