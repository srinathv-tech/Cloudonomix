from flask import Flask, jsonify
from logic.optimization_engine import (
    detect_idle_resources,
    suggest_action
)
from backend.data import get_vm_data
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


from flask import request

@app.route("/summary")
def summary():
    cloud = request.args.get("cloud")  # AWS / Azure / GCP / None
    vms = get_vm_data()

    total_cost = 0
    wasted_cost = 0
    active_resources = 0

    for vm in vms:
        if cloud and vm["provider"] != cloud:
            continue

        vm_cost = vm["hours_running"] * vm["cost_per_hour"]
        total_cost += vm_cost
        active_resources += 1

        if vm["cpu_usage"] < 10 and vm["hours_running"] > 6:
            wasted_cost += vm_cost

    return jsonify({
        "total_cost": total_cost,
        "wasted_cost": wasted_cost,
        "active_resources": active_resources
    })


@app.route("/idle-resources")
def idle_resources():
    vms = get_vm_data()
    idle_vms = detect_idle_resources(vms)

    for vm in idle_vms:
        vm["suggested_action"] = suggest_action(vm)

    return jsonify(idle_vms)


@app.route("/all-resources")
def all_resources():
    vms = get_vm_data()

    for vm in vms:
        vm["suggested_action"] = suggest_action(vm)

    return jsonify(vms)

from backend.data import get_vm_data

@app.route("/cloud-distribution")
def cloud_distribution():
    cloud = request.args.get("cloud")
    vms = get_vm_data()

    cloud_costs = {}
    wasted_cost = 0

    for vm in vms:
        if cloud and vm["provider"] != cloud:
            continue

        provider = vm["provider"]
        vm_cost = vm["hours_running"] * vm["cost_per_hour"]

        cloud_costs[provider] = cloud_costs.get(provider, 0) + vm_cost

        if vm["cpu_usage"] < 10 and vm["hours_running"] > 6:
            wasted_cost += vm_cost

    highest = max(cloud_costs, key=cloud_costs.get) if cloud_costs else "-"
    lowest = min(cloud_costs, key=cloud_costs.get) if cloud_costs else "-"

    return jsonify({
        "distribution": cloud_costs,
        "highest": highest,
        "lowest": lowest,
        "total_clouds": len(cloud_costs),
        "wasted_cost": wasted_cost
    })






if __name__ == "__main__":
    app.run(debug=True)


