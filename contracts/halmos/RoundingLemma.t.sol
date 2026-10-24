// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

/// @notice The rounding lemma as a Halmos query over 256-bit words. Expected outcome: TIMEOUT (yices, 60 s per
///         assertion; the same sums asserted at the end of the pool path did not finish in 900 s either). Kept so the
///         report shows the attempt; the
///         lemma itself is proved over unbounded integers by `rounding_lemma.py` (Z3), which is exact here because
///         cap < 2^128 and shares <= 10,000 never wrap a uint256.
contract RoundingLemmaHalmos {
    function check_rounding(uint128 cap, uint16 a, uint16 b, uint16 c) public pure {
        require(a > 0 && b > 0 && c > 0 && uint256(a) + b + c == 10_000);
        uint256 sum = uint256(cap) * a / 10_000 + uint256(cap) * b / 10_000 + uint256(cap) * c / 10_000;
        assert(sum <= cap);
        assert(cap - sum <= 2);
    }

    function check_rounding_policy(uint128 cap) public pure {
        uint256 sum = uint256(cap) * 3000 / 10_000 + uint256(cap) * 3000 / 10_000 + uint256(cap) * 4000 / 10_000;
        assert(sum <= cap);
        assert(cap - sum <= 2);
    }
}
