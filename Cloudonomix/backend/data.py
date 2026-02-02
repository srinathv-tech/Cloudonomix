def get_vm_data():
    return [
        {
            "vm_id": "vm-101",
            "provider": "AWS",
            "cpu_usage": 5,
            "hours_running": 10,
            "cost_per_hour": 90
        },
        {
            "vm_id": "vm-102",
            "provider": "AWS",
            "cpu_usage": 85,
            "hours_running": 20,
            "cost_per_hour": 20
        },
        {
            "vm_id": "vm-201",
            "provider": "Azure",
            "cpu_usage": 15,
            "hours_running": 12,
            "cost_per_hour": 60
        },
        {
            "vm_id":"vm_202",
            "provider": "GCP",
            "cpu_usage": 20,
            "hours_running": 24,
            "cost_per_hour": 45
        }
    ]
