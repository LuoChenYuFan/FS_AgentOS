"""Each catalog skill is a folder with a markdown note and a script."""

from fs_agent_runtime.registry import load_catalog
from fs_agent_runtime.skills.loader import load_skills, run_skill


def test_every_registered_skill_has_a_folder_a_note_and_a_script() -> None:
    catalog = load_catalog()
    found = load_skills()
    assert set(found) == catalog.skill_names()
    for spec in found.values():
        assert spec.directory.name == spec.name
        assert (spec.directory / "SKILL.md").is_file()
        assert spec.entrypoint.is_file()
        assert spec.description


def test_route_hotel_and_math_scripts_return_their_fields() -> None:
    route = run_skill("查路线", {"起点": "西直门地铁站", "终点": "北京南站", "方式": "乘车"})
    assert route["fare"] == 35
    hotel = run_skill("查酒店", {"城市": "广州", "星级": 5, "日期": "明天"})
    assert hotel["avg_price"] == 1850
    assert run_skill("subtract", {"left": 100, "right": 40}) == {"value": 60}
    assert run_skill("amount_from_text", {"text": "差旅住宿不超过800元"}) == {"amount": 800}
