// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;
import "./SupplyChain.sol";

/// @notice Individual immutable proofs alongside the existing shipment checkpoints.
contract SupplyChainAudit is SupplyChain {
    struct ReadingRecord {
        bytes32 packageIdHash;
        bytes32 dataHash;
        uint256 timestamp;
        bool alert;
        bool exists;
    }
    mapping(bytes32 => ReadingRecord) private readings;
    event ReadingAnchored(bytes32 indexed packageIdHash, bytes32 indexed readingIdHash,
        bytes32 dataHash, uint256 timestamp, bool alert);

    function anchorReading(bytes32 packageIdHash, bytes32 readingIdHash, bytes32 dataHash,
        uint256 timestamp, bool alert) external onlyRole(WRITER_ROLE) {
        require(packageIdHash != bytes32(0) && readingIdHash != bytes32(0) && dataHash != bytes32(0), "Empty hash");
        require(!readings[readingIdHash].exists, "Reading already anchored");
        readings[readingIdHash] = ReadingRecord(packageIdHash, dataHash, timestamp, alert, true);
        emit ReadingAnchored(packageIdHash, readingIdHash, dataHash, timestamp, alert);
    }
    function getReading(bytes32 readingIdHash) external view returns (ReadingRecord memory) {
        return readings[readingIdHash];
    }
    function readingExists(bytes32 readingIdHash) external view returns (bool) {
        return readings[readingIdHash].exists;
    }
}
