// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @notice Permissioned classroom shipment ledger. Frequent telemetry stays off-chain.
contract SupplyChain {
    address public immutable owner;
    struct Record { bytes32 packageKey; bytes32 dataHash; uint8 kind; uint64 recordedAt; }
    mapping(bytes32 => bytes32) public registrations;
    mapping(bytes32 => Record) public records;
    event Anchored(bytes32 indexed eventKey, bytes32 indexed packageKey, uint8 kind, bytes32 dataHash);

    constructor() { owner = msg.sender; }

    modifier onlyOwner() {
        require(msg.sender == owner, "Unauthorized writer");
        _;
    }

    /// @dev kind: 0 registration, 1 condition change, 2 checkpoint.
    /// An identical replay is a no-op; conflicting replay always fails.
    function anchor(bytes32 eventKey, bytes32 packageKey, uint8 kind, bytes32 dataHash) external onlyOwner {
        require(eventKey != bytes32(0) && packageKey != bytes32(0) && dataHash != bytes32(0), "Empty key or hash");
        require(kind <= 2, "Invalid kind");
        Record memory previous = records[eventKey];
        if (previous.dataHash != bytes32(0)) {
            require(previous.packageKey == packageKey && previous.dataHash == dataHash && previous.kind == kind, "Conflicting replay");
            return;
        }
        if (kind == 0) {
            require(registrations[packageKey] == bytes32(0), "Already registered");
            registrations[packageKey] = dataHash;
        } else {
            require(registrations[packageKey] != bytes32(0), "Unknown shipment");
        }
        records[eventKey] = Record(packageKey, dataHash, kind, uint64(block.timestamp));
        emit Anchored(eventKey, packageKey, kind, dataHash);
    }
}
