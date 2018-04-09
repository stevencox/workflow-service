import threading
import tempfile
import subprocess
import uuid
import os
import json
import urllib
import sys
from wes_service.util import visit, WESBackend

class Workflow(object):
    def __init__(self, workflow_id):
        super(Workflow, self).__init__()
        self.workflow_id = workflow_id
        self.workdir = os.path.join(os.getcwd(), "workflows", self.workflow_id)

    def run(self, request, opts):
        os.makedirs(self.workdir)
        outdir = os.path.join(self.workdir, "outdir")
        os.mkdir(outdir)

        with open(os.path.join(self.workdir, "request.json"), "w") as f:
            json.dump(request, f)

        with open(os.path.join(self.workdir, "cwl.input.json"), "w") as inputtemp:
            json.dump(request["workflow_params"], inputtemp)

        if request.get("workflow_descriptor"):
            with open(os.path.join(self.workdir, "workflow.cwl"), "w") as f:
                f.write(workflow_descriptor)
                workflow_url = urllib.pathname2url(os.path.join(self.workdir, "workflow.cwl"))
        else:
            workflow_url = request.get("workflow_url")

        output = open(os.path.join(self.workdir, "cwl.output.json"), "w")
        stderr = open(os.path.join(self.workdir, "stderr"), "w")

        runner = opts.getopt("runner", "cwl-runner")
        extra = opts.getoptlist("extra")

        proc = subprocess.Popen([runner]+extra+[workflow_url, inputtemp.name],
                                stdout=output,
                                stderr=stderr,
                                close_fds=True,
                                cwd=outdir)
        output.close()
        stderr.close()
        with open(os.path.join(self.workdir, "pid"), "w") as pid:
            pid.write(str(proc.pid))

        return self.getstatus()

    def getstate(self):
        state = "RUNNING"
        exit_code = -1

        exc = os.path.join(self.workdir, "exit_code")
        if os.path.exists(exc):
            with open(exc) as f:
                exit_code = int(f.read())
        elif os.path.exists(os.path.join(self.workdir, "pid")):
            with open(os.path.join(self.workdir, "pid"), "r") as pid:
                pid = int(pid.read())
            try:
                (_pid, exit_status) = os.waitpid(pid, os.WNOHANG)
                if _pid != 0:
                    exit_code = exit_status >> 8
                    with open(exc, "w") as f:
                        f.write(str(exit_code))
                    os.unlink(os.path.join(self.workdir, "pid"))
            except OSError as e:
                os.unlink(os.path.join(self.workdir, "pid"))
                exit_code = 255

        if exit_code == 0:
            state = "COMPLETE"
        elif exit_code != -1:
            state = "EXECUTOR_ERROR"

        return (state, exit_code)

    def getstatus(self):
        state, exit_code = self.getstate()

        return {
            "workflow_id": self.workflow_id,
            "state": state
        }

    def getlog(self):
        state, exit_code = self.getstate()

        with open(os.path.join(self.workdir, "request.json"), "r") as f:
            request = json.load(f)

        with open(os.path.join(self.workdir, "stderr"), "r") as f:
            stderr = f.read()

        outputobj = {}
        if state == "COMPLETE":
            with open(os.path.join(self.workdir, "cwl.output.json"), "r") as outputtemp:
                outputobj = json.load(outputtemp)

        return {
            "workflow_id": self.workflow_id,
            "request": request,
            "state": state,
            "workflow_log": {
                "cmd": [""],
                "start_time": "",
                "end_time": "",
                "stdout": "",
                "stderr": stderr,
                "exit_code": exit_code
            },
            "task_logs": [],
            "outputs": outputobj
        }

    def cancel(self):
        pass


class CWLRunnerBackend(WESBackend):
    # Aliases to handle Swagger Router Controler in spec
    aliases = {
        'ga4gh.wes.server.GetServiceInfo': 'GetServiceInfo',
        'ga4gh.wes.server.ListWorkflows': 'ListWorkflows',
        'ga4gh.wes.server.RunWorkflow': 'RunWorkflow',
        'ga4gh.wes.server.GetWorkflowLog': 'GetWorkflowLog',
        'ga4gh.wes.server.CancelJob': 'CancelJob',
        'ga4gh.wes.server.GetWorkflowStatus': 'GetWorkflowStatus',
    }

    def __setattr__(self, name, value):
        name = self.aliases.get(name, name)
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        if name == "aliases":
            raise AttributeError
        name = self.aliases.get(name, name)
        return object.__getattribute__(self, name)

    def GetServiceInfo(self):
        return {
            "workflow_type_versions": { "workflow_type_version": { "CWL":["v1.0"] } },
            "supported_wes_versions": ["0.2.0"],
            "supported_filesystem_protocols": ["file"],
            "workflow_engine_versions": {"cwl-runner":"1.0"},
            "default_workflow_engine_parameters": [
                {
                    "type":"string",
                    "default_value":"None"
                }
            ],
            "system_state_counts": {},
            "auth_instructions_url": "",
            "tags": {}
        }

    def ListWorkflows(self ,body=None):
        # body["page_size"]
        # body["page_token"]
        # body["tag_search"]

        wf = []
        for l in os.listdir(os.path.join(os.getcwd(), "workflows")):
            if os.path.isdir(os.path.join(os.getcwd(), "workflows", l)):
                wf.append(Workflow(l))
        return {
            "workflows": [{"workflow_id": w.workflow_id, "state": w.getstate()[0]} for w in wf],
            "next_page_token": ""
        }

    def RunWorkflow(self, body):
        if body["workflow_type"] != "CWL" or body["workflow_type_version"] != "v1.0":
            return
        workflow_id = uuid.uuid4().hex
        job = Workflow(workflow_id)
        job.run(body, self)
        return {"workflow_id": workflow_id}

    def GetWorkflowLog(self, workflow_id):
        job = Workflow(workflow_id)
        return job.getlog()

    def CancelJob(self, workflow_id):
        job = Workflow(workflow_id)
        job.cancel()
        return {"workflow_id": workflow_id}

    def GetWorkflowStatus(self, workflow_id):
        job = Workflow(workflow_id)
        return job.getstatus()

def create_backend(opts):
    return CWLRunnerBackend(opts)
