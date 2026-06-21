# File: backend/test_browserbase.py
# BROWSERBASE TEST SUITE (Windows-compatible version)
# Note: Unicode emoji/symbols removed to avoid Windows cp1252 encoding errors

import asyncio
from submission_agent import submit_to_payer_portal


async def test_successful_submission():
    """Test a successful submission"""
    print("\n" + "="*80)
    print("TEST 1: SUCCESSFUL SUBMISSION")
    print("="*80 + "\n")

    result = await submit_to_payer_portal(
        request_id="TEST_SUCCESS_001",
        patient_name="Jane Smith",
        member_id="123-45-6789",
        procedure_code="70553",
        diagnosis_code="M17.11"
    )

    print(f"Status: {result.get('status')}")
    print(f"Confirmation: {result.get('confirmation_number')}")
    print(f"Submitted at: {result.get('submitted_at')}")

    assert result.get('status') == 'success', "Should be success"
    assert result.get('confirmation_number') is not None, "Should have confirmation"
    print("\n[PASS] TEST PASSED\n")
    return True


async def test_multiple_submissions():
    """Test multiple submissions in sequence"""
    print("="*80)
    print("TEST 2: MULTIPLE SUBMISSIONS")
    print("="*80 + "\n")

    test_cases = [
        {
            'name': 'Cardiology',
            'patient': 'John Doe',
            'member_id': '987-65-4321',
            'procedure': '93000',
            'diagnosis': 'I10'
        },
        {
            'name': 'Orthopedic',
            'patient': 'Mary Johnson',
            'member_id': '555-55-5555',
            'procedure': '70553',
            'diagnosis': 'M17.11'
        },
        {
            'name': 'Psychiatry',
            'patient': 'Robert Williams',
            'member_id': '111-11-1111',
            'procedure': '99213',
            'diagnosis': 'F41.1'
        }
    ]

    for i, case in enumerate(test_cases, 1):
        print(f"{i}. Testing {case['name']}...")

        result = await submit_to_payer_portal(
            request_id=f"TEST_MULTI_{i:03d}",
            patient_name=case['patient'],
            member_id=case['member_id'],
            procedure_code=case['procedure'],
            diagnosis_code=case['diagnosis']
        )

        print(f"   Status: {result.get('status')}")
        print(f"   Confirmation: {result.get('confirmation_number')}\n")

        assert result.get('status') == 'success', f"Test {i} should succeed"

    print("[PASS] ALL TESTS PASSED\n")
    return True


async def test_with_special_characters():
    """Test with special characters / hyphens in patient name"""
    print("="*80)
    print("TEST 3: SPECIAL CHARACTERS IN PATIENT NAME")
    print("="*80 + "\n")

    result = await submit_to_payer_portal(
        request_id="TEST_SPECIAL_001",
        patient_name="Maria Garcia-Lopez",
        member_id="999-99-9999",
        procedure_code="70553",
        diagnosis_code="M17.11"
    )

    print(f"Patient: Maria Garcia-Lopez")
    print(f"Status: {result.get('status')}")
    print(f"Confirmation: {result.get('confirmation_number')}\n")

    assert result.get('status') == 'success', "Should handle special characters"
    print("[PASS] TEST PASSED\n")
    return True


async def main():
    print("\nBROWSERBASE SUBMISSION AGENT TEST SUITE\n")

    try:
        test1_result = await test_successful_submission()
        test2_result = await test_multiple_submissions()
        test3_result = await test_with_special_characters()

        if test1_result and test2_result and test3_result:
            print("="*80)
            print("[PASS] ALL TESTS PASSED - SUBMISSION AGENT IS WORKING")
            print("="*80)
            print("\nReady for integration with Orkes and demo!\n")

    except Exception as e:
        print(f"\n[FAIL] TEST FAILED: {str(e)}\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
