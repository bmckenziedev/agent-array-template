"""Reference receiver authentication; no dependency on a particular console."""


def verify(token, hello, token_review, namespace_lookup, project, user_ns_prefix, label_prefix):
    audience = project + "-supervisor"
    status = token_review(token, [audience]).get("status", {})
    if not isinstance(status, dict) or status.get("authenticated") is not True or status.get("audiences") != [audience]:
        raise PermissionError("audience")
    user = status.get("user", {})
    parts = user.get("username", "").split(":")
    if len(parts) != 4 or parts[:2] != ["system", "serviceaccount"] or parts[3] != "session":
        raise PermissionError("serviceaccount")
    namespace = parts[2]
    if not namespace.startswith(user_ns_prefix):
        raise PermissionError("namespace_prefix")
    labels = namespace_lookup(namespace).get("metadata", {}).get("labels", {})
    if labels.get(label_prefix + "/kind") != "user-sessions":
        raise PermissionError("namespace_kind")
    if not hello.get("user") or labels.get(label_prefix + "/user") != hello["user"]:
        raise PermissionError("holder")
    names = user.get("extra", {}).get("authentication.kubernetes.io/pod-name", [])
    if names != [hello.get("pod")]:
        raise PermissionError("pod_binding")
    return {"namespace": namespace, "user": hello["user"], "pod": hello["pod"]}
