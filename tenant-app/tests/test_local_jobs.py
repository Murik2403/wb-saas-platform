from local_jobs import LocalJobs


def test_job_failure_does_not_hide_other_success():
    jobs = LocalJobs({})
    def failure():
        raise ValueError("sensitive detail")
    jobs.execute("bad", failure)
    jobs.execute("good", lambda: None)
    state = jobs.status()
    assert state["bad"]["status"] == "error"
    assert state["bad"]["error"] == "ValueError"
    assert state["good"]["status"] == "success"
    assert "sensitive" not in str(state)
