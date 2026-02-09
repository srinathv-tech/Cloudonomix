def detect_idle_resources(vms):
    idle=[]
    for vm in vms:
        if vm["cpu_usage"]<10 and vm["hours_running"]>6:
            idle.append(vm)
    return idle
    
def detect_cost_anomaly(today_cost,yesterday_cost):
    if today_cost>1.3*yesterday_cost:
        return True
    return False

def suggest_action(vm):
    if vm["cpu_usage"]<10:
        return "AutoHibernate Recommended"
    elif vm["cpu_usage"]>80:
        return "Resize to higher Capacity"
    else:
        return "No Action Needed"
    
def analyze_resources(vms, cloud=None):
    total_cost = 0
    wasted_cost = 0
    active_resources = 0
    cloud_distribution = {}
    idle_vms = []

    for vm in vms:
        if vm.get("status") == "stopped":
            continue
        
        if cloud and vm["provider"] != cloud:
            continue

        cost = vm["hours_running"] * vm["cost_per_hour"]
        total_cost += cost
        active_resources += 1

        cloud_distribution[vm["provider"]] = (
            cloud_distribution.get(vm["provider"], 0) + cost
        )

        # idle logic
        if vm["cpu_usage"] < 10 and vm["hours_running"] > 6:
            wasted_cost += cost
            idle_vms.append({
                "cloud": vm["provider"],
                "name": vm["vm_id"],
                "status": "Idle",
                "action": suggest_action(vm)
            })

    alerts = []

    if total_cost > 75000:
        alerts.append("Monthly spend exceeded ₹75,000")

    if wasted_cost > 0:
        alerts.append("Over-provisioned resources identified")

    if len(idle_vms) > 1:
        alerts.append("Multiple idle virtual machines detected")

    highest_cloud = max(cloud_distribution, key=cloud_distribution.get)
    lowest_cloud = min(cloud_distribution, key=cloud_distribution.get)

    return {
        "total_cost": total_cost,
        "wasted_cost": wasted_cost,
        "active_resources": active_resources,
        "cloud_distribution": cloud_distribution,
        "highest_cost_cloud": highest_cloud,
        "lowest_cost_cloud": lowest_cloud,
        "idle_vms": len(idle_vms),
        "idle_vm_list": idle_vms,
        "potential_savings": wasted_cost,
        "alerts": alerts
    }



