const assert = require('node:assert/strict');
const { ethers } = require('hardhat');
const key = value => ethers.sha256(ethers.toUtf8Bytes(value));

describe('SupplyChain', function () {
  let contract, other;
  beforeEach(async function () {
    [, other] = await ethers.getSigners();
    contract = await (await ethers.getContractFactory('SupplyChain')).deploy();
    await contract.waitForDeployment();
  });
  const register = async c => (await c.anchor(key('registration'), key('PKG001'), 0, key('metadata'))).wait();

  it('registers shipment hashes and records emitted evidence', async function () {
    const receipt = await register(contract);
    assert.equal(await contract.registrations(key('PKG001')), key('metadata'));
    const record = await contract.records(key('registration'));
    assert.equal(record.kind, 0n);
    assert.equal(record.dataHash, key('metadata'));
    assert.equal(receipt.logs.length, 1);
  });
  it('rejects unauthorized writes', async function () {
    await assert.rejects(contract.connect(other).anchor(key('a'), key('b'), 0, key('c')), /Unauthorized writer/);
  });
  it('requires shipment registration before conditions and checkpoints', async function () {
    await assert.rejects(contract.anchor(key('a'), key('PKG001'), 1, key('b')), /Unknown shipment/);
  });
  it('stores conditions and checkpoints', async function () {
    await register(contract);
    for (const kind of [1, 2]) {
      await (await contract.anchor(key(`e${kind}`), key('PKG001'), kind, key(`h${kind}`))).wait();
      assert.equal((await contract.records(key(`e${kind}`))).dataHash, key(`h${kind}`));
    }
  });
  it('makes identical retries idempotent', async function () {
    await register(contract);
    const receipt = await register(contract);
    assert.equal(receipt.logs.length, 0);
  });
  it('rejects conflicting retries and duplicate registrations', async function () {
    await register(contract);
    await assert.rejects(contract.anchor(key('registration'), key('PKG001'), 0, key('changed')), /Conflicting replay/);
    await assert.rejects(contract.anchor(key('another'), key('PKG001'), 0, key('metadata')), /Already registered/);
  });
  it('rejects zero hashes and invalid event kinds', async function () {
    await assert.rejects(contract.anchor(ethers.ZeroHash, key('p'), 0, key('h')), /Empty key or hash/);
    await assert.rejects(contract.anchor(key('e'), key('p'), 3, key('h')), /Invalid kind/);
  });
});
