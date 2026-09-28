import multiprocessing

from gzh_reader.task_worker import run_collect_task


def test_spawned_worker_reports_model_error_without_leaking_key(tmp_path):
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    secret = "请填写真实密钥"
    process = context.Process(target=run_collect_task, args=({
        "url": "https://mp.weixin.qq.com/s/test", "output": str(tmp_path),
        "model_provider": "doubao", "model_api_key": secret,
        "agent_mode": True,
    }, events))
    process.start()
    event = events.get(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 0
    assert event["kind"] == "final" and event["stage"] == "failed"
    assert "ARK_API_KEY 格式无效" in event["detail"]["error"]
    assert secret not in str(event)
