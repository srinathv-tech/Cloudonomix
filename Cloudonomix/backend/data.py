VM_DATA = [
        {
            "vm_id": "vm-101",
            "provider": "AWS",
            "cpu_usage": 5,
            "hours_running": 10,
            "cost_per_hour": 90,
            "status": "running"
        },
        {
            "vm_id": "vm-102",
            "provider": "AWS",
            "cpu_usage": 85,
            "hours_running": 20,
            "cost_per_hour": 20,
            "status": "running"
        },
        {
            "vm_id": "vm-201",
            "provider": "Azure",
            "cpu_usage": 15,
            "hours_running": 12,
            "cost_per_hour": 60,
            "status": "running"
        },
        {
            "vm_id":"vm_202",
            "provider": "GCP",
            "cpu_usage": 20,
            "hours_running": 24,
            "cost_per_hour": 45,
            "status": "running"
        },
        {
            "vm_id":"vm_301",
            "provider": "GCP",
            "cpu_usage": 8,
            "hours_running": 24,
            "cost_per_hour": 20,
            "status": "running"
        }
    ]

def get_vm_data():
    return VM_DATA