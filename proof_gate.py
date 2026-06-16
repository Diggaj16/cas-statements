import json
from fastapi.testclient import TestClient
import main
from unittest.mock import patch

# Mock parse_cas to return the contents of parsed_debug.json
with open('parsed_debug.json', 'r', encoding='utf-8') as f:
    parsed_data_mock = json.load(f)

def mock_parse_cas(file_path, password):
    return parsed_data_mock

@patch('main.parse_cas', side_effect=mock_parse_cas)
def run_test(mock_pc):
    client = TestClient(main.app)
    # Dummy file upload
    dummy_file = ("dummy.pdf", b"fake pdf content", "application/pdf")
    response = client.post(
        "/api/analyze",
        files={"cas_file": dummy_file},
        data={"cas_password": "dummy_password"}
    )
    
    data = response.json()
    
    if response.status_code != 200:
        print("Error:", data)
        exit(1)
    
    # Extract golden values
    golden = {
        "liveXirr": data.get("liveXirr"),
        "casXirr": data.get("casXirr"),
        "exclTax.liveXirr": data.get("exclTax", {}).get("liveXirr"),
        "exclTax.casXirr": data.get("exclTax", {}).get("casXirr"),
        "fundWise": [
            {"Scheme": f["Scheme"], "LiveXIRR": f["LiveXIRR"]}
            for f in data.get("fundWise", [])
        ]
    }
    
    with open("golden_xirr.json", "w") as f:
        json.dump(golden, f, indent=2)
    
    print("Golden numbers saved to golden_xirr.json!")

if __name__ == "__main__":
    run_test()
