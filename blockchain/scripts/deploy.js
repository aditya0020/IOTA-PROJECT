const hre = require('hardhat');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const [admin, localWriter] = await hre.ethers.getSigners();
  const writer = process.env.BLOCKCHAIN_WRITER_ADDRESS || (hre.network.name === 'localhost' ? localWriter.address : '');
  if (!hre.ethers.isAddress(writer) || writer === hre.ethers.ZeroAddress || writer.toLowerCase() === admin.address.toLowerCase()) throw new Error('Configure a distinct BLOCKCHAIN_WRITER_ADDRESS');
  const factory = await hre.ethers.getContractFactory('SupplyChainAudit');
  const contract = await factory.deploy();
  await contract.waitForDeployment();
  await (await contract.grantRole(await contract.WRITER_ROLE(), writer)).wait();
  await (await contract.revokeRole(await contract.WRITER_ROLE(), admin.address)).wait();
  const artifact = await hre.artifacts.readArtifact('SupplyChainAudit');
  const address = await contract.getAddress();
  const network = await hre.ethers.provider.getNetwork();
  const receipt = await contract.deploymentTransaction().wait();
  const deployment = { address, chain_id: Number(network.chainId), block_number: receipt.blockNumber,
    owner: await contract.owner(), writer, transaction_hash: receipt.hash, abi: artifact.abi };
  fs.writeFileSync(path.join(__dirname, '..', 'deployment.json'), JSON.stringify(deployment, null, 2) + '\n');
  console.log(`SupplyChainAudit deployed at ${address}; network ${hre.network.name}; chain ${network.chainId}; transaction ${receipt.hash}; writer ${writer}`);
}
main().catch(() => { console.error('Deployment failed. Check network, deployer funding and writer configuration.'); process.exitCode = 1; });
