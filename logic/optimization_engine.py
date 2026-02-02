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
    



