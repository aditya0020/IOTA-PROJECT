const assert = require('node:assert/strict');
const { ethers } = require('hardhat');
const key = value => ethers.sha256(ethers.toUtf8Bytes(value));
describe('SupplyChainAudit', function () {
  let audit, admin, writer, outsider;
  beforeEach(async function () {
    [admin, writer, outsider] = await ethers.getSigners();
    audit = await (await ethers.getContractFactory('SupplyChainAudit')).deploy();
    await audit.waitForDeployment();
    await (await audit.grantRole(await audit.WRITER_ROLE(), writer.address)).wait();
    await (await audit.revokeRole(await audit.WRITER_ROLE(), admin.address)).wait();
  });
  const anchor = c => c.anchorReading(key('package'), key('reading'), key('data'), 1234, true);
  it('separates admin and writer and rejects unauthorized role grants', async function () {
    assert.equal(await audit.hasRole(await audit.DEFAULT_ADMIN_ROLE(), admin.address), true);
    assert.equal(await audit.hasRole(await audit.DEFAULT_ADMIN_ROLE(), writer.address), false);
    await assert.rejects(audit.connect(writer).grantRole(await audit.WRITER_ROLE(), outsider.address));
    await assert.rejects(anchor(audit.connect(outsider)));
    await assert.rejects(anchor(audit));
  });
  it('anchors all fields, looks up existence and emits the exact event', async function () {
    assert.equal(await audit.readingExists(key('reading')), false);
    const receipt = await (await anchor(audit.connect(writer))).wait();
    assert.equal(await audit.readingExists(key('reading')), true);
    assert.deepEqual(Array.from(await audit.getReading(key('reading'))), [key('package'), key('data'), 1234n, true, true]);
    const log = audit.interface.parseLog(receipt.logs[0]);
    assert.equal(log.name, 'ReadingAnchored');
    assert.deepEqual(Array.from(log.args), [key('package'), key('reading'), key('data'), 1234n, true]);
  });
  it('rejects identical and conflicting duplicates without overwriting', async function () {
    await (await anchor(audit.connect(writer))).wait();
    await assert.rejects(anchor(audit.connect(writer)), /Reading already anchored/);
    await assert.rejects(audit.connect(writer).anchorReading(key('package'), key('reading'), key('changed'), 9999, false));
    assert.equal((await audit.getReading(key('reading'))).dataHash, key('data'));
  });
  it('revokes writer access and rejects empty hashes', async function () {
    await assert.rejects(audit.connect(writer).anchorReading(ethers.ZeroHash, key('reading'), key('data'), 1, false));
    await (await audit.revokeRole(await audit.WRITER_ROLE(), writer.address)).wait();
    await assert.rejects(anchor(audit.connect(writer)));
  });
});
